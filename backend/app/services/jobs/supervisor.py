"""CHR-31 / CHR-WORKER-001 spawn-only process execution supervisor."""

import json
import multiprocessing
import os
import struct
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from multiprocessing.connection import Connection
from time import monotonic
from typing import Any

from app.services.jobs.handlers import HANDLERS, dispatch
from app.services.jobs.outcomes import DOMAIN_OUTCOMES, JobDomainError, JobOrphaned

_PROTOCOL_VERSION = 1
_POLL_INTERVAL_SECONDS = 0.01
_PUBLIC_FAILURE = "job execution failed"
_PUBLIC_CANCELLED = "job execution cancelled"


class JobOutcome(StrEnum):
    SUCCESS = "success"
    EXCEPTION = "exception"
    DOMAIN_ERROR = "domain_error"
    CRASH = "crash"
    PROTOCOL_ERROR = "protocol_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    outcome: JobOutcome
    data: dict[str, Any] | None = None
    error: str | None = None
    # Set only for an allowlisted domain outcome; see outcomes.DOMAIN_OUTCOMES.
    code: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is JobOutcome.SUCCESS


def execute_job(
    kind: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    kill_grace_seconds: float = 0.25,
    cancellation_requested: Callable[[], bool] | None = None,
) -> JobExecutionResult:
    """Execute one allowlisted job and return only sanitized public state."""
    # The allowlist is the handler table, so a new job kind cannot be
    # dispatchable in the child yet refused here.
    if kind not in HANDLERS:
        raise ValueError("unsupported job kind")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if kill_grace_seconds < 0:
        raise ValueError("kill_grace_seconds must not be negative")

    # Reject values that would silently depend on pickle semantics at the IPC edge.
    encoded_payload = json.dumps(dict(payload), allow_nan=False).encode("utf-8")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child_main, args=(send, kind, encoded_payload))
    deadline = monotonic() + timeout_seconds
    message: bytes | None = None
    started = False
    result: JobExecutionResult | None = None
    unreaped = False

    try:
        if _is_cancelled(cancellation_requested):
            result = JobExecutionResult(
                JobOutcome.CANCELLED,
                error=_PUBLIC_CANCELLED,
            )
            return result
        process.start()
        started = True
        send.close()
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                result = JobExecutionResult(
                    JobOutcome.TIMEOUT,
                    error="job execution timed out",
                )
                break
            # Drain the pipe first: a response already buffered in the pipe
            # wins over a cancellation that was just requested. A large frame
            # still in transfer can lose to cancellation (see _read_exact).
            if receive.poll(min(_POLL_INTERVAL_SECONDS, remaining)):
                message, result = _receive_message(
                    receive, deadline, cancellation_requested
                )
                break
            if not process.is_alive():
                if receive.poll():
                    message, result = _receive_message(
                        receive, deadline, cancellation_requested
                    )
                break
            if _is_cancelled(cancellation_requested):
                result = JobExecutionResult(
                    JobOutcome.CANCELLED,
                    error=_PUBLIC_CANCELLED,
                )
                break
        else:
            raise AssertionError("unreachable")

        if message is not None:
            process.join(max(0.0, deadline - monotonic()))
            if process.is_alive():
                result = JobExecutionResult(
                    JobOutcome.PROTOCOL_ERROR,
                    error="job process did not exit after responding",
                )
            else:
                result = _decode_message(message)
        elif result is not None:
            # Cancellation and timeout results were selected in the polling loop.
            pass
        else:
            process.join(max(0.0, deadline - monotonic()))
            if process.is_alive():
                result = JobExecutionResult(
                    JobOutcome.PROTOCOL_ERROR,
                    error="job process did not exit without responding",
                )
            else:
                result = JobExecutionResult(
                    JobOutcome.CRASH,
                    error="job process exited abruptly",
                )
    finally:
        if started and process.is_alive():
            process.terminate()
            process.join(kill_grace_seconds)
        if started and process.is_alive():
            process.kill()
            process.join(kill_grace_seconds)
        if started and process.is_alive():
            unreaped = True
        receive.close()
        send.close()

    if unreaped:
        return JobExecutionResult(
            JobOutcome.PROTOCOL_ERROR,
            error="job process could not be reaped",
        )
    if result is None:
        return _protocol_error()
    return result


class _ReceiveStopped(Exception):
    def __init__(self, result: JobExecutionResult) -> None:
        self.result = result


def _receive_message(
    receive: Connection,
    deadline: float,
    cancellation_requested: Callable[[], bool] | None,
) -> tuple[bytes | None, JobExecutionResult | None]:
    # poll() only means some bytes are readable; recv_bytes() would block on a
    # partial frame past the deadline, so read the frame with bounded waits.
    try:
        header = _read_exact(receive, 4, deadline, cancellation_requested)
    except EOFError:
        return None, None
    except _ReceiveStopped as stopped:
        return None, stopped.result
    except OSError:
        return None, _protocol_error()
    try:
        (size,) = struct.unpack("!i", header)
        if size == -1:
            (size,) = struct.unpack(
                "!Q", _read_exact(receive, 8, deadline, cancellation_requested)
            )
        return _read_exact(receive, size, deadline, cancellation_requested), None
    except _ReceiveStopped as stopped:
        return None, stopped.result
    except (EOFError, OSError):
        # A truncated frame is a protocol failure, not a missing response.
        return None, _protocol_error()


def _read_exact(
    receive: Connection,
    size: int,
    deadline: float,
    cancellation_requested: Callable[[], bool] | None,
) -> bytes:
    data = bytearray()
    next_cancel_check = monotonic() + _POLL_INTERVAL_SECONDS
    while len(data) < size:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _ReceiveStopped(
                JobExecutionResult(JobOutcome.TIMEOUT, error="job execution timed out")
            )
        ready = receive.poll(min(_POLL_INTERVAL_SECONDS, remaining))
        # Also check while bytes keep arriving, so a trickling child cannot
        # starve cancellation; a promptly sent frame still wins.
        if not ready or monotonic() >= next_cancel_check:
            if _is_cancelled(cancellation_requested):
                raise _ReceiveStopped(
                    JobExecutionResult(JobOutcome.CANCELLED, error=_PUBLIC_CANCELLED)
                )
            next_cancel_check = monotonic() + _POLL_INTERVAL_SECONDS
        if not ready:
            continue
        # Cap each read: the declared size is child-controlled and may be absurd.
        chunk = os.read(receive.fileno(), min(size - len(data), 1 << 16))
        if not chunk:
            if data:
                raise OSError("got end of file during message")
            raise EOFError
        data += chunk
    return bytes(data)


def _watch_parent() -> None:
    """CHR-197: end this child as soon as its supervisor dies (no publishing).

    The GA is one blocking call with no progress hook, so a daemon thread waits
    on the parent's sentinel (portable; no polling, no pid-reuse race).
    """
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch() -> None:
        multiprocessing.connection.wait([parent.sentinel])
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _child_main(send: Connection, kind: str, payload: bytes) -> None:
    _watch_parent()
    try:
        try:
            decoded_payload = _decode_json_object(payload)
            data = dispatch(kind, decoded_payload)
            json.dumps(data, allow_nan=False)
            message = {
                "protocol_version": _PROTOCOL_VERSION,
                "status": "success",
                "data": data,
            }
        except JobOrphaned:
            return
        except JobDomainError as error:
            message = {
                "protocol_version": _PROTOCOL_VERSION,
                "status": "domain_error",
                "code": error.code,
            }
        except Exception:
            message = {
                "protocol_version": _PROTOCOL_VERSION,
                "status": "exception",
                "error": _PUBLIC_FAILURE,
            }
        send.send_bytes(
            json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8")
        )
    finally:
        send.close()


def _decode_message(message: bytes) -> JobExecutionResult:
    try:
        decoded = _decode_json_object(message)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return _protocol_error()
    if type(decoded.get("protocol_version")) is not int:
        return _protocol_error()
    if decoded.get("protocol_version") != _PROTOCOL_VERSION:
        return _protocol_error()
    if set(decoded) == {"protocol_version", "status", "error"} and (
        decoded.get("status") == "exception"
        and decoded.get("error") == _PUBLIC_FAILURE
    ):
        return JobExecutionResult(JobOutcome.EXCEPTION, error=_PUBLIC_FAILURE)
    if set(decoded) == {"protocol_version", "status", "code"} and (
        decoded.get("status") == "domain_error"
    ):
        code = decoded.get("code")
        if not isinstance(code, str) or code not in DOMAIN_OUTCOMES:
            return _protocol_error()
        return JobExecutionResult(
            JobOutcome.DOMAIN_ERROR, error=DOMAIN_OUTCOMES[code], code=code
        )
    if set(decoded) != {"protocol_version", "status", "data"}:
        return _protocol_error()
    if decoded.get("status") != "success":
        return _protocol_error()
    data = decoded.get("data")
    if not isinstance(data, dict):
        return _protocol_error()
    return JobExecutionResult(JobOutcome.SUCCESS, data=data)


def _decode_json_object(value: bytes) -> dict[str, Any]:
    decoded = json.loads(
        value.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(decoded, dict):
        raise ValueError("JSON value must be an object")
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_cancelled(cancellation_requested: Callable[[], bool] | None) -> bool:
    if cancellation_requested is None:
        return False
    try:
        return bool(cancellation_requested())
    except Exception:
        return True


def _protocol_error() -> JobExecutionResult:
    return JobExecutionResult(
        JobOutcome.PROTOCOL_ERROR,
        error="invalid job process response",
    )
