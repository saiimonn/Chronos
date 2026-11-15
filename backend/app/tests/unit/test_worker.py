"""CHR-31 / CHR-WORKER-001 worker orchestration tests."""

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import app.worker as worker
import pytest
from app.services.jobs import JobExecutionResult, JobOutcome


@pytest.mark.parametrize("kind", ["ga", "arm", "evaluation"])
@pytest.mark.parametrize("stored_identity", [False, True])
def test_stop_skips_terminal_update_for_requeue(monkeypatch, kind, stored_identity):
    events: list[str] = []
    stop_event = threading.Event()
    job = SimpleNamespace(
        job_id="job-1",
        claim_token="claim-1",
        job_type=SimpleNamespace(value=kind),
        request={"version": 3},
        timeout_seconds=10,
    )
    if stored_identity:
        job.request.update(job_id="untrusted-job", claim_token="old-claim")
    original_request = job.request.copy()
    observed_payloads = []

    class Repository:
        def recover_stale(self):
            events.append("recover")

        def claim_next(self, **_kwargs):
            return job

    class Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    @contextmanager
    def heartbeats(_job):
        events.append("heartbeat-start")
        try:
            yield threading.Event()
        finally:
            events.append("heartbeat-stop")

    def supervise(actual_kind, payload, *, cancellation_requested, **_kwargs):
        observed_payloads.append((actual_kind, payload))
        assert not cancellation_requested()
        events.append("supervise")
        stop_event.set()
        assert cancellation_requested()
        return JobExecutionResult(
            JobOutcome.CANCELLED,
            error="job execution cancelled",
        )

    def finish(_job, outcome):
        events.append("finish")

    monkeypatch.setattr(worker.SessionLocal, "begin", lambda: Transaction())
    monkeypatch.setattr(worker, "_repository", lambda _session: Repository())
    monkeypatch.setattr(worker, "_heartbeats", heartbeats)
    monkeypatch.setattr(worker, "_supervisor", lambda: supervise)
    monkeypatch.setattr(worker, "_finish", finish)

    worker.run(stop_event)

    actual_kind, payload = observed_payloads[0]
    assert actual_kind == kind
    assert payload == {"job_id": "job-1", "claim_token": "claim-1", "version": 3}
    assert payload is not job.request
    assert "supervise" in events
    assert "heartbeat-stop" in events
    assert "finish" not in events
    assert job.request == original_request


@pytest.mark.parametrize(("kind", "timed"), [("ga", True), ("arm", False), ("evaluation", False)])
def test_successful_ga_attempt_records_its_processing_seconds(monkeypatch, kind, timed):
    # CHR-137 / CHR-EVAL-013: claim to terminal persistence of this attempt only.
    # The real _finish runs; a slow row lock stands in for delayed persistence.
    stop_event = threading.Event()
    job = SimpleNamespace(
        job_id="job-1",
        claim_token="claim-1",
        job_type=SimpleNamespace(value=kind),
        request={"version": 3},
        timeout_seconds=10,
    )
    completed = []

    class Repository:
        def recover_stale(self):
            pass

        def claim_next(self, **_kwargs):
            return job

        def lock_live_claim(self, job_id, claim_token):
            assert (job_id, claim_token) == ("job-1", "claim-1")
            time.sleep(0.05)
            return True

        def complete(self, _job_id, _claim_token, *, result):
            completed.append(result)
            stop_event.set()
            return object()

    class Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    @contextmanager
    def heartbeats(_job):
        yield threading.Event()

    def supervise(*_args, **_kwargs):
        time.sleep(0.02)
        return JobExecutionResult(JobOutcome.SUCCESS, data={"metrics": {"elapsed_seconds": 0.01}})

    monkeypatch.setattr(worker.SessionLocal, "begin", lambda: Transaction())
    monkeypatch.setattr(worker, "_repository", lambda _session: Repository())
    monkeypatch.setattr(worker, "_heartbeats", heartbeats)
    monkeypatch.setattr(worker, "_supervisor", lambda: supervise)

    worker.run(stop_event)

    data = completed[0]
    assert data["metrics"] == {"elapsed_seconds": 0.01}
    if timed:
        # Engine wait (0.02) plus the delayed terminal persistence (0.05).
        assert data["processing_seconds"] >= 0.07
    else:
        assert "processing_seconds" not in data


def test_lost_claim_at_terminal_persistence_records_no_processing_seconds(monkeypatch):
    completed = []

    class Repository:
        def lock_live_claim(self, *_args):
            return False

        def complete(self, _job_id, _claim_token, *, result):
            completed.append(result)
            return None

    class Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(worker.SessionLocal, "begin", lambda: Transaction())
    monkeypatch.setattr(worker, "_repository", lambda _session: Repository())
    job = SimpleNamespace(job_id="job-1", claim_token="claim-1")

    worker._finish(job, JobExecutionResult(JobOutcome.SUCCESS, data={}), claimed_at=0.0)

    assert completed == [{}]


@pytest.mark.parametrize(
    "outcome",
    [
        JobExecutionResult(JobOutcome.EXCEPTION, error="job execution failed"),
        JobExecutionResult(JobOutcome.TIMEOUT, error="timed out"),
    ],
)
def test_failed_attempts_get_no_processing_seconds(outcome):
    assert worker._with_processing_seconds(outcome, claimed_at=0.0) is outcome


def _fast_heartbeats(monkeypatch, outcomes):
    """Run heartbeats with a fake clock: each attempt takes one second.

    The lease is 10 s and the interval tiny, so a failure is retried only
    while one more 1 s attempt would still end before the lease does.
    """
    clock = [0.0]
    calls = []

    def heartbeat(*_args):
        clock[0] += 1
        result = outcomes(len(calls))
        calls.append(clock[0])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker, "_heartbeat", heartbeat)
    monkeypatch.setattr(worker.settings, "job_heartbeat_interval_seconds", 0.001)
    monkeypatch.setattr(worker.settings, "job_lease_seconds", 10)
    monkeypatch.setattr(worker.settings, "job_termination_grace_seconds", 1)
    job = SimpleNamespace(job_id="job-1", claim_token="claim-1")
    return worker._heartbeats(job), calls


DOWN = RuntimeError("database unavailable")


def test_lost_lease_cancels_at_once(monkeypatch):
    heartbeats, calls = _fast_heartbeats(monkeypatch, lambda _n: False)
    with heartbeats as cancelled:
        assert cancelled.wait(1)
    assert calls == [1]


def test_short_heartbeat_failure_does_not_cancel(monkeypatch):
    heartbeats, calls = _fast_heartbeats(
        monkeypatch, lambda n: DOWN if n < 5 else True
    )
    with heartbeats as cancelled:
        give_up = time.monotonic() + 2
        while len(calls) < 30 and time.monotonic() < give_up:
            assert not cancelled.wait(0.001)
    assert len(calls) >= 30
    assert not cancelled.is_set()


def test_failure_lasting_until_lease_deadline_cancels(monkeypatch):
    heartbeats, calls = _fast_heartbeats(monkeypatch, lambda _n: DOWN)
    with heartbeats as cancelled:
        assert cancelled.wait(1)
    # The attempt ending at 9 s cancels: a 10th would end at the deadline.
    assert calls == [float(t) for t in range(1, 10)]


def test_failure_deadline_counts_from_last_successful_heartbeat(monkeypatch):
    heartbeats, calls = _fast_heartbeats(
        monkeypatch, lambda n: True if n < 5 else DOWN
    )
    with heartbeats as cancelled:
        assert cancelled.wait(1)
    # The 5th attempt renewed from second 4, so the deadline is second 14.
    assert calls[-1] == 13


def test_lost_lease_after_a_failure_still_cancels_at_once(monkeypatch):
    heartbeats, calls = _fast_heartbeats(
        monkeypatch, lambda n: DOWN if n == 0 else False
    )
    with heartbeats as cancelled:
        assert cancelled.wait(1)
    assert calls == [1, 2]


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (
            JobExecutionResult(
                JobOutcome.DOMAIN_ERROR,
                error="No pattern met the thresholds.",
                code="arm_no_rules_retained",
            ),
            "arm_no_rules_retained",
        ),
        (
            JobExecutionResult(JobOutcome.EXCEPTION, error="job execution failed"),
            "execution_exception",
        ),
    ],
)
def test_finish_records_domain_codes_and_keeps_crashes_generic(
    monkeypatch, outcome, expected_code
):
    failures: list[dict] = []

    class Repository:
        def fail(self, _job_id, _claim_token, **kwargs):
            failures.append(kwargs)
            return object()

    class Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(worker.SessionLocal, "begin", lambda: Transaction())
    monkeypatch.setattr(worker, "_repository", lambda _session: Repository())

    worker._finish(SimpleNamespace(job_id="job-1", claim_token="claim-1"), outcome)

    assert failures == [
        {"error_code": expected_code, "error_message": outcome.error}
    ]

