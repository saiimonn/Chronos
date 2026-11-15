"""CHR-31 / CHR-WORKER-001 spawn supervisor failure-mode tests."""

import os
import json
import struct
import subprocess
import time
import types
from multiprocessing.connection import Connection

import pytest

import app.services.jobs.supervisor as supervisor
from app.services.ga.config import GAConfig
from app.services.jobs import JobOutcome, encode_ga_payload, execute_job
from app.services.jobs.outcomes import DOMAIN_OUTCOMES, JobDomainError
from app.tests.support.scheduling import make_scheduling_problem


def _hard_crash(_send: Connection, _kind: str, _payload: bytes) -> None:
    os._exit(23)


def _invalid_protocol(send: Connection, _kind: str, _payload: bytes) -> None:
    send.send_bytes(
        json.dumps({"protocol_version": -1, "status": "success", "data": {}}).encode(
            "utf-8"
        )
    )
    send.close()


def _slow_child(_send: Connection, _kind: str, _payload: bytes) -> None:
    time.sleep(30)


def _partial_frame_stall(send: Connection, _kind: str, _payload: bytes) -> None:
    # Declare a 100-byte frame, send 3 bytes, then hold the pipe open.
    os.write(send.fileno(), struct.pack("!i", 100) + b'{"p')
    time.sleep(30)


def _truncated_frame(send: Connection, _kind: str, _payload: bytes) -> None:
    os.write(send.fileno(), struct.pack("!i", 100) + b'{"p')
    os._exit(0)


def _absurd_frame_size(send: Connection, _kind: str, _payload: bytes) -> None:
    os.write(send.fileno(), struct.pack("!i", -1) + struct.pack("!Q", 2**63))
    os._exit(0)


def _small_payload() -> dict:
    return encode_ga_payload(
        make_scheduling_problem(),
        GAConfig(
            population_size=2,
            max_generations=1,
            tournament_size=2,
            elite_count=0,
            random_seed=7,
            meeting_day_patterns=("MW",),
        ),
    )


def test_supervisor_returns_success_from_spawned_ga_child():
    os.environ["GA_SKIP_PERSIST"] = "1"
    try:
        result = execute_job("ga", _small_payload(), timeout_seconds=10)
    finally:
        os.environ.pop("GA_SKIP_PERSIST", None)

    assert result.outcome is JobOutcome.SUCCESS
    assert result.succeeded
    assert result.data["candidate"]["assignments"][0]["meeting_days"] == "MW"


def test_supervisor_runs_an_evaluation_job_in_a_spawned_child():
    """The worker path, not just dispatch(), must accept evaluation jobs."""
    from app.services.arm.providers import GuidanceRule, HistoricalGuidance
    from app.services.arm.preprocessing import PatternStream
    from app.services.evaluation import input_fingerprint
    from app.services.jobs.codecs import (
        CODEC_VERSION,
        encode_ga_config,
        encode_guidance_snapshot,
        encode_scheduling_problem,
    )
    from app.tests.support.scheduling import FACULTY_ID
    from decimal import Decimal
    from uuid import uuid4

    problem = encode_scheduling_problem(make_scheduling_problem())
    guidance = encode_guidance_snapshot(
        HistoricalGuidance(
            rules=(
                GuidanceRule(
                    stream=PatternStream.TEACHER_SUBJECT,
                    faculty_id=FACULTY_ID,
                    subject_code="<SUBJECT-1>",
                    room_code=None,
                    meeting_days=None,
                    time_slot=None,
                    guidance_score=Decimal("0.8000"),
                    support=Decimal("0.5000"),
                    confidence=Decimal("0.7500"),
                    lift=Decimal("1.2000"),
                    term_recurrence=3,
                ),
            ),
            rule_set_id=uuid4(),
            version=1,
        )
    )
    payload = {
        "version": CODEC_VERSION,
        "kind": "evaluation",
        "problem": problem,
        "historical_guidance": guidance,
        "config": encode_ga_config(
            GAConfig(
                population_size=2,
                max_generations=1,
                tournament_size=2,
                elite_count=0,
                meeting_day_patterns=("MW",),
            )
        ),
        "seeds": list(range(10)),
        "input_fingerprint": input_fingerprint(problem, guidance),
    }
    os.environ["GA_SKIP_PERSIST"] = "1"
    try:
        result = execute_job("evaluation", payload, timeout_seconds=60)
    finally:
        os.environ.pop("GA_SKIP_PERSIST", None)

    assert result.outcome is JobOutcome.SUCCESS
    assert result.data["succeeded_run_count"] == 20
    assert result.data["failed_run_count"] == 0


def test_every_job_type_is_allowlisted_by_the_supervisor():
    from app.models import JobType

    assert {kind.value for kind in JobType} == set(supervisor.HANDLERS)


def test_supervisor_sanitizes_ordinary_child_exception():
    payload = _small_payload()
    del payload["problem"]

    result = execute_job("ga", payload, timeout_seconds=10)

    assert result.outcome is JobOutcome.EXCEPTION
    assert result.error == "job execution failed"
    assert result.data is None


def test_supervisor_classifies_abrupt_child_crash(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _hard_crash)

    result = execute_job("ga", _small_payload(), timeout_seconds=10)

    assert result.outcome is JobOutcome.CRASH
    assert result.error == "job process exited abruptly"


def test_supervisor_classifies_invalid_child_protocol(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _invalid_protocol)

    result = execute_job("ga", _small_payload(), timeout_seconds=10)

    assert result.outcome is JobOutcome.PROTOCOL_ERROR
    assert result.error == "invalid job process response"


def test_supervisor_strictly_rejects_malformed_protocol_messages():
    messages = [
        b"\xff",
        b'{"protocol_version":1,"protocol_version":1,"status":"success","data":{}}',
        b'{"protocol_version":1,"status":"success","data":{},"extra":true}',
        b'{"protocol_version":true,"status":"success","data":{}}',
    ]

    for message in messages:
        result = supervisor._decode_message(message)
        assert result.outcome is JobOutcome.PROTOCOL_ERROR


def test_supervisor_carries_only_allowlisted_domain_outcomes():
    known = supervisor._decode_message(
        b'{"protocol_version":1,"status":"domain_error","code":"arm_no_rules_retained"}'
    )
    rejected = [
        b'{"protocol_version":1,"status":"domain_error","code":"made_up"}',
        b'{"protocol_version":1,"status":"domain_error","code":7}',
        b'{"protocol_version":1,"status":"domain_error","code":"arm_no_rules_retained",'
        b'"error":"child-chosen text"}',
    ]

    assert known.outcome is JobOutcome.DOMAIN_ERROR
    assert known.code == "arm_no_rules_retained"
    # The public text comes from the parent's table, never from the child.
    assert known.error == DOMAIN_OUTCOMES["arm_no_rules_retained"]
    for message in rejected:
        assert supervisor._decode_message(message).outcome is JobOutcome.PROTOCOL_ERROR


def test_unknown_domain_outcome_code_cannot_be_raised():
    with pytest.raises(ValueError):
        JobDomainError("made_up")


def test_supervisor_terminates_timed_out_child():
    original_child = supervisor._child_main
    supervisor._child_main = _slow_child
    try:
        result = execute_job("ga", _small_payload(), timeout_seconds=0.2)
    finally:
        supervisor._child_main = original_child

    assert result.outcome is JobOutcome.TIMEOUT
    assert result.error == "job execution timed out"


def test_supervisor_times_out_child_stalled_mid_frame(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _partial_frame_stall)

    started = time.monotonic()
    result = execute_job("ga", _small_payload(), timeout_seconds=1)

    assert result.outcome is JobOutcome.TIMEOUT
    assert time.monotonic() - started < 5


def test_supervisor_cancels_child_stalled_mid_frame(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _partial_frame_stall)
    stalled = False
    real_read_exact = supervisor._read_exact

    def read_exact(receive, size, *args):
        nonlocal stalled
        stalled = size == 100
        return real_read_exact(receive, size, *args)

    monkeypatch.setattr(supervisor, "_read_exact", read_exact)

    result = execute_job(
        "ga",
        _small_payload(),
        timeout_seconds=10,
        cancellation_requested=lambda: stalled,
    )

    assert result.outcome is JobOutcome.CANCELLED


def test_trickling_frame_cannot_starve_cancellation(monkeypatch):
    class AlwaysReadable:
        def poll(self, _timeout):
            return True

        def fileno(self):
            return -1

    def trickle(_fd, _size):
        time.sleep(0.001)
        return b" "

    # One byte per millisecond: poll() never times out.
    monkeypatch.setattr(supervisor, "os", types.SimpleNamespace(read=trickle))
    started = time.monotonic()

    with pytest.raises(supervisor._ReceiveStopped) as stopped:
        supervisor._read_exact(
            AlwaysReadable(),
            100_000,
            started + 5,
            lambda: time.monotonic() - started > 0.1,
        )

    assert stopped.value.result.outcome is JobOutcome.CANCELLED


def test_supervisor_treats_absurd_frame_size_as_protocol_error(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _absurd_frame_size)

    result = execute_job("ga", _small_payload(), timeout_seconds=10)

    assert result.outcome is JobOutcome.PROTOCOL_ERROR


def test_supervisor_treats_truncated_frame_as_protocol_error(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _truncated_frame)

    result = execute_job("ga", _small_payload(), timeout_seconds=10)

    assert result.outcome is JobOutcome.PROTOCOL_ERROR
    assert result.error == "invalid job process response"


def test_supervisor_cancels_started_child(monkeypatch):
    monkeypatch.setattr(supervisor, "_child_main", _slow_child)
    polls = 0

    def cancellation_requested() -> bool:
        nonlocal polls
        polls += 1
        return polls > 2

    result = execute_job(
        "ga",
        _small_payload(),
        timeout_seconds=10,
        cancellation_requested=cancellation_requested,
    )

    assert result.outcome is JobOutcome.CANCELLED
    assert result.error == "job execution cancelled"


def test_supervisor_drains_large_response_while_child_is_alive():
    payload = _small_payload()
    payload["config"]["meeting_day_patterns"] = ["MWF"] * 250_000

    os.environ["GA_SKIP_PERSIST"] = "1"
    try:
        result = execute_job(
            "ga",
            payload,
            timeout_seconds=20,
        )
    finally:
        os.environ.pop("GA_SKIP_PERSIST", None)

    assert result.outcome is JobOutcome.SUCCESS
    assert len(result.data["metrics"]["config"]["meeting_day_patterns"]) == 250_000


# CHR-197 / CHR-FIX-025: an orphaned child must not publish and must exit.


def test_orphaned_child_ends_without_publishing_or_replying(monkeypatch):
    from app.services.jobs import handlers, outcomes

    published = []
    monkeypatch.setattr(
        handlers, "_run_ga_term", lambda _p: outcomes.ensure_parent_alive() or published.append(1)
    )
    monkeypatch.setattr(outcomes.multiprocessing, "parent_process",
                        lambda: types.SimpleNamespace(is_alive=lambda: False))
    # The fake parent has no sentinel; don't start the real watchdog thread.
    monkeypatch.setattr(supervisor, "_watch_parent", lambda: None)
    sent = []
    send = types.SimpleNamespace(send_bytes=sent.append, close=lambda: None)

    supervisor._child_main(send, "ga", b'{"version": 1}')

    assert published == [] and sent == []


def test_orphan_check_blocks_ga_persist_but_live_parent_publishes(monkeypatch):
    from app.services.jobs import handlers, outcomes

    monkeypatch.delenv("GA_SKIP_PERSIST", raising=False)
    monkeypatch.setattr(outcomes.multiprocessing, "parent_process",
                        lambda: types.SimpleNamespace(is_alive=lambda: False))
    with pytest.raises(outcomes.JobOrphaned):
        handlers._persist_ga_schedule(None, None, None, {})

    # Live parent: passes the orphan check and reaches the claim fence.
    monkeypatch.setattr(outcomes.multiprocessing, "parent_process",
                        lambda: types.SimpleNamespace(is_alive=lambda: True))
    with pytest.raises(ValueError, match="live worker claim"):
        handlers._persist_ga_schedule(None, None, None, {})


def _sleep_after_watch(pidfile: str) -> None:
    supervisor._watch_parent()
    with open(pidfile, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(60)


def _spawn_orphan_candidate(pidfile: str) -> None:
    import multiprocessing

    multiprocessing.get_context("spawn").Process(
        target=_sleep_after_watch, args=(pidfile,)
    ).start()
    time.sleep(60)


def _read_pid(pidfile: str) -> int | None:
    try:
        with open(pidfile) as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    return int(text) if text else None


def _is_running(pid: int) -> bool:
    """True unless the pid is gone or a zombie (os.kill(pid, 0) passes for both)."""
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_child_exits_when_its_supervisor_is_killed(tmp_path):
    import multiprocessing
    import signal

    pidfile = str(tmp_path / "child.pid")
    parent = multiprocessing.get_context("spawn").Process(
        target=_spawn_orphan_candidate, args=(pidfile,)
    )
    parent.start()
    child_pid = None
    try:
        deadline = time.monotonic() + 20
        while (child_pid := _read_pid(pidfile)) is None:
            assert time.monotonic() < deadline, "child never started"
            time.sleep(0.05)
        os.kill(parent.pid, signal.SIGKILL)
        parent.join()

        deadline = time.monotonic() + 5
        while _is_running(child_pid):
            assert time.monotonic() < deadline, (
                "orphaned child kept running after its supervisor died"
            )
            time.sleep(0.05)
    finally:
        if parent.is_alive():
            parent.kill()
            parent.join()
        if child_pid is None:
            child_pid = _read_pid(pidfile)
        if child_pid is not None and _is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)
