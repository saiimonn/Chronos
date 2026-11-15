import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AppUser, ExecutionJob, JobStatus, JobType, UserRole
from app.repositories.jobs import ExecutionJobRecord, JobRepository


pytestmark = pytest.mark.postgres


@pytest.fixture
def requester_id(migrated_engine):
    user_id = uuid.uuid4()
    with Session(migrated_engine) as session, session.begin():
        session.add(
            AppUser(
                user_id=user_id,
                username=f"job-requester-{user_id}",
                password_hash="$argon2id$test-only-placeholder",
                role=UserRole.SCHEDULING_COMMITTEE,
            )
        )
    try:
        yield user_id
    finally:
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                text("DELETE FROM execution_job WHERE requested_by = :user_id"),
                {"user_id": user_id},
            )
            session.execute(
                text("DELETE FROM app_user WHERE user_id = :user_id"),
                {"user_id": user_id},
            )


def enqueue(migrated_engine, requester_id, **overrides) -> ExecutionJobRecord:
    values = {
        "job_type": JobType.GA,
        "requested_by": requester_id,
        "request": {"term_id": str(uuid.uuid4())},
        "timeout_seconds": 120,
        "max_attempts": 2,
    }
    values.update(overrides)
    with Session(migrated_engine) as session, session.begin():
        return JobRepository(session).enqueue(**values)


def test_job_lifecycle_uses_detached_records_and_fenced_fresh_sessions(
    migrated_engine, requester_id
):
    request = {"seed": 7}
    queued = enqueue(migrated_engine, requester_id, request=request)
    request["seed"] = 99

    assert isinstance(queued, ExecutionJobRecord)
    assert queued.status is JobStatus.QUEUED
    assert queued.request == {"seed": 7}

    with Session(migrated_engine) as session, session.begin():
        claimed = JobRepository(session).claim_next(
            claimed_by="worker-1", lease_seconds=30
        )
    assert claimed is not None
    assert claimed.job_id == queued.job_id
    assert claimed.attempt_count == 1
    assert claimed.claim_token is not None

    with Session(migrated_engine) as session, session.begin():
        rejected = JobRepository(session).heartbeat(
            claimed.job_id,
            uuid.uuid4(),
            lease_seconds=30,
            progress={"generation": 1},
        )
    assert rejected is None

    with Session(migrated_engine) as session, session.begin():
        heartbeat = JobRepository(session).heartbeat(
            claimed.job_id,
            claimed.claim_token,
            lease_seconds=30,
            progress={"generation": 2},
        )
    assert heartbeat is not None
    assert heartbeat.progress == {"generation": 2}

    with Session(migrated_engine) as session, session.begin():
        completed = JobRepository(session).complete(
            claimed.job_id, claimed.claim_token, result={"schedule_id": "result-1"}
        )
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result == {"schedule_id": "result-1"}
    assert completed.claim_token is None

    with Session(migrated_engine) as session:
        loaded = JobRepository(session).get(queued.job_id)
    assert loaded == completed


def test_stale_recovery_requeues_then_fails_exhausted_job(
    migrated_engine, requester_id
):
    queued = enqueue(migrated_engine, requester_id, max_attempts=2)

    with Session(migrated_engine) as session, session.begin():
        first_claim = JobRepository(session).claim_next(
            claimed_by="worker-1", lease_seconds=30
        )
        assert first_claim is not None
        session.execute(
            text(
                "UPDATE execution_job SET lease_expires_at = "
                "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
            ),
            {"job_id": queued.job_id},
        )

    with Session(migrated_engine) as session, session.begin():
        recovery = JobRepository(session).recover_stale()
    assert recovery.requeued == 1
    assert recovery.failed == 0

    with Session(migrated_engine) as session, session.begin():
        second_claim = JobRepository(session).claim_next(
            claimed_by="worker-2", lease_seconds=30
        )
        assert second_claim is not None
        assert second_claim.attempt_count == 2
        session.execute(
            text(
                "UPDATE execution_job SET lease_expires_at = "
                "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
            ),
            {"job_id": queued.job_id},
        )

    with Session(migrated_engine) as session, session.begin():
        recovery = JobRepository(session).recover_stale()
    assert recovery.requeued == 0
    assert recovery.failed == 1

    with Session(migrated_engine) as session:
        failed = JobRepository(session).get(queued.job_id)
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "worker_lease_exhausted"
    assert failed.claim_token is None


@pytest.mark.parametrize(
    ("operation", "expected_status"),
    [("fail", JobStatus.FAILED), ("timeout", JobStatus.TIMED_OUT)],
)
def test_error_terminal_updates_are_token_fenced(
    migrated_engine, requester_id, operation, expected_status
):
    queued = enqueue(migrated_engine, requester_id)
    with Session(migrated_engine) as session, session.begin():
        claimed = JobRepository(session).claim_next(
            claimed_by="worker-1", lease_seconds=30
        )
    assert claimed is not None

    with Session(migrated_engine) as session, session.begin():
        repository = JobRepository(session)
        if operation == "fail":
            rejected = repository.fail(
                queued.job_id,
                uuid.uuid4(),
                error_code="invalid_input",
                error_message="The request snapshot was invalid.",
            )
            finished = repository.fail(
                queued.job_id,
                claimed.claim_token,
                error_code="invalid_input",
                error_message="The request snapshot was invalid.",
            )
        else:
            rejected = repository.timeout(queued.job_id, uuid.uuid4())
            finished = repository.timeout(queued.job_id, claimed.claim_token)

    assert rejected is None
    assert finished is not None
    assert finished.status is expected_status
    assert finished.error_code is not None
    assert finished.error_message is not None


def test_claim_next_skips_a_row_locked_by_another_worker(
    migrated_engine, requester_id
):
    first = enqueue(migrated_engine, requester_id, job_type=JobType.ARM)
    second = enqueue(migrated_engine, requester_id, job_type=JobType.ARM)

    session_one = Session(migrated_engine)
    session_two = Session(migrated_engine)
    try:
        session_one.begin()
        claimed_one = JobRepository(session_one).claim_next(
            claimed_by="worker-1", lease_seconds=30, job_types=[JobType.ARM]
        )
        session_two.begin()
        claimed_two = JobRepository(session_two).claim_next(
            claimed_by="worker-2", lease_seconds=30, job_types=[JobType.ARM]
        )

        assert claimed_one is not None
        assert claimed_two is not None
        assert {claimed_one.job_id, claimed_two.job_id} == {first.job_id, second.job_id}
    finally:
        session_two.rollback()
        session_one.rollback()
        session_two.close()
        session_one.close()


@pytest.mark.parametrize("operation", ["heartbeat", "complete"])
@pytest.mark.parametrize("release_owner", ["commit", "rollback"])
def test_waiting_fenced_update_rejects_claim_that_expired_behind_lock(
    migrated_engine, requester_id, operation, release_owner
):
    """CHR-126 / CHR-ARM-006: clock checks must follow a lock-only wait."""
    queued = enqueue(migrated_engine, requester_id)
    with Session(migrated_engine) as session, session.begin():
        claimed = JobRepository(session).claim_next(
            claimed_by="fenced-update-test", lease_seconds=5
        )
    assert claimed is not None and claimed.job_id == queued.job_id
    assert claimed.claim_token is not None
    ready = Event()
    contender_pids = []

    def update_after_lock():
        with Session(migrated_engine) as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            contender_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            repository = JobRepository(session)
            if operation == "heartbeat":
                return repository.heartbeat(
                    claimed.job_id, claimed.claim_token,
                    lease_seconds=30, progress={"generation": 1},
                )
            return repository.complete(
                claimed.job_id, claimed.claim_token, result={"test": True}
            )

    # Three simultaneous connections: lock owner, contender, clock/lock observer.
    with Session(migrated_engine) as owner:
        owner_pid = owner.scalar(text("SELECT pg_backend_pid()"))
        assert owner.scalar(
            select(ExecutionJob.job_id)
            .where(ExecutionJob.job_id == claimed.job_id)
            .with_for_update()
        ) == claimed.job_id
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(update_after_lock)
            try:
                assert ready.wait(5), "contender did not start"
                with Session(migrated_engine) as observer:
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        blockers = observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": contender_pids[0]},
                        )
                        if owner_pid in blockers:
                            break
                        if future.done():
                            future.result()
                            pytest.fail("fenced update did not wait on the owner lock")
                        sleep(0.01)
                    else:
                        pytest.fail("contender never reached the row lock")
                    assert observer.scalar(
                        text("SELECT clock_timestamp() < :deadline"),
                        {"deadline": claimed.lease_expires_at},
                    ), "claim expired before lock contention was established"
                    deadline = monotonic() + 6
                    while not observer.scalar(
                        text("SELECT clock_timestamp() >= :deadline"),
                        {"deadline": claimed.lease_expires_at},
                    ):
                        assert monotonic() < deadline, "database clock did not advance"
                        sleep(0.05)
                # No UPDATE by the owner: releasing its lock cannot cause a
                # pre-wait predicate to be reevaluated through EvalPlanQual.
                getattr(owner, release_owner)()
                assert future.result(timeout=10) is None
            finally:
                owner.rollback()

    with Session(migrated_engine) as session:
        assert JobRepository(session).get(claimed.job_id) == claimed
