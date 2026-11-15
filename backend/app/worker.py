"""CHR-31 / CHR-WORKER-001 local durable GA/ARM worker entry point."""

import logging
import os
import signal
import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from time import monotonic, perf_counter
from typing import Any

from app.core.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _repository(session):
    from app.repositories.jobs import JobRepository

    return JobRepository(session)


def _supervisor():
    from app.services.jobs.supervisor import execute_job

    return execute_job


def _heartbeat(job_id, claim_token) -> bool:
    with SessionLocal.begin() as session:
        return _repository(session).heartbeat(
            job_id,
            claim_token,
            lease_seconds=settings.job_lease_seconds,
        ) is not None


@contextmanager
def _heartbeats(job: Any):
    stopped = threading.Event()
    cancelled = threading.Event()

    interval = settings.job_heartbeat_interval_seconds
    # CHR-184 / CHR-FIX-014: the lease was set at claim, just before this.
    last_renewed = monotonic()

    def send_heartbeats() -> None:
        nonlocal last_renewed
        while not stopped.wait(interval):
            attempt_started = monotonic()
            try:
                if not _heartbeat(job.job_id, job.claim_token):
                    logger.warning("job lease was lost job_id=%s", job.job_id)
                    cancelled.set()
                    return
                # The DB set the new deadline during the attempt, not after.
                last_renewed = attempt_started
            except Exception:
                # A transient DB error is retried while the lease is still
                # held; cancel once the next attempt, as slow as this one,
                # would end too late.
                now = monotonic()
                next_attempt_ends = now + interval + (now - attempt_started)
                if next_attempt_ends < last_renewed + settings.job_lease_seconds:
                    logger.warning(
                        "job heartbeat failed, retrying job_id=%s",
                        job.job_id,
                        exc_info=True,
                    )
                    continue
                logger.exception("job heartbeat failed job_id=%s", job.job_id)
                cancelled.set()
                return

    thread = threading.Thread(target=send_heartbeats, daemon=True)
    thread.start()
    try:
        yield cancelled
    finally:
        stopped.set()
        thread.join(settings.job_termination_grace_seconds)
        if thread.is_alive():
            logger.error("job heartbeat thread did not stop job_id=%s", job.job_id)


def _with_processing_seconds(outcome: Any, claimed_at: float) -> Any:
    """CHR-137 / CHR-EVAL-013: full-system time of the attempt that succeeded.

    Spans this claim through process start, decoding, the GA and, for a
    term-wide job, schedule validation and persistence, into the terminal
    persistence: ``_finish`` samples it after its transaction has opened and
    the job row lock is held, so a slow terminal write is counted. Only the
    final fenced result UPDATE on that already-locked row and the COMMIT
    round-trip are excluded, because the value is stored by that write. A
    per-Block schedule is written later, at replacement confirm. Measured per
    attempt, so a retried job does not count its dead attempts or queue wait;
    unlike the engine-only ``metrics.elapsed_seconds`` (thesis Table 10).

    A term-wide job completed by reusing a schedule its crashed earlier attempt
    published (CHR-192) reports the reclaim attempt's time here and is flagged
    ``reused_published_result``; exclude such jobs from timing analyses or
    report them separately. ``published_attempt_processing_seconds`` gives the
    crashed attempt's in-process time when known (input decode through GA and
    scoring; it excludes schedule validation and persistence).
    """
    if not outcome.succeeded or outcome.data is None:
        return outcome
    return replace(
        outcome,
        data={**outcome.data, "processing_seconds": perf_counter() - claimed_at},
    )


def _finish(job: Any, outcome: Any, claimed_at: float | None = None) -> None:
    with SessionLocal.begin() as session:
        repository = _repository(session)
        if outcome.succeeded:
            if claimed_at is not None and repository.lock_live_claim(
                job.job_id, job.claim_token
            ):
                # Sample under the row lock; complete() re-locks it in this transaction.
                outcome = _with_processing_seconds(outcome, claimed_at)
            updated = repository.complete(
                job.job_id, job.claim_token, result=outcome.data
            )
        elif outcome.outcome.value == "timeout":
            updated = repository.timeout(job.job_id, job.claim_token)
        else:
            updated = repository.fail(
                job.job_id,
                job.claim_token,
                error_code=outcome.code or f"execution_{outcome.outcome.value}",
                error_message=outcome.error or "Job execution failed.",
            )
        if updated is None:
            logger.warning("fenced terminal update rejected job_id=%s", job.job_id)


def run(stop_event: threading.Event | None = None) -> None:
    """Poll forever; one malformed or failed job must not stop later work."""
    stop_event = stop_event or threading.Event()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    supervisor = _supervisor()

    while not stop_event.is_set():
        try:
            with SessionLocal.begin() as session:
                repository = _repository(session)
                repository.recover_stale()
                job = repository.claim_next(
                    claimed_by=worker_id,
                    lease_seconds=settings.job_lease_seconds,
                )
            if job is None:
                stop_event.wait(settings.job_poll_interval_seconds)
                continue

            claimed_at = perf_counter()
            with _heartbeats(job) as heartbeat_cancelled:
                # Claim identity belongs to this attempt, not the stored request.
                payload = {
                    **job.request,
                    "job_id": str(job.job_id),
                    "claim_token": str(job.claim_token),
                }
                outcome = supervisor(
                    job.job_type.value,
                    payload,
                    timeout_seconds=job.timeout_seconds,
                    kill_grace_seconds=settings.job_termination_grace_seconds,
                    cancellation_requested=lambda: (
                        stop_event.is_set() or heartbeat_cancelled.is_set()
                    ),
                )
                # On graceful shutdown (stop_event), skip terminal update so
                # the lease expires and stale-lease recovery requeues the job.
                if outcome.outcome.value == "cancelled" and stop_event.is_set():
                    logger.info(
                        "skipping terminal update on shutdown job_id=%s",
                        job.job_id,
                    )
                else:
                    _finish(
                        job,
                        outcome,
                        claimed_at if job.job_type.value == "ga" else None,
                    )
        except Exception:
            logger.exception("job worker iteration failed")
            stop_event.wait(settings.job_poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run(stop_event)


if __name__ == "__main__":
    main()
