"""CHR-165 / CHR-FIX-005 live-claim-fenced GA schedule publication."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Timer
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.domain import CandidateSchedule
from app.models import (
    AppUser, BlockSection, BlockSectionOffering, CourseProgram, ExecutionJob,
    Faculty, JobStatus, JobType, Room, Schedule, ScheduleRecord, Subject, SubjectOffering,
    Term, TimeSlot, UserRole,
)
from app.repositories.jobs import JobRepository
from app.services.schedules import persist_candidate
from app.tests.support.scheduling import (
    TERM_ID, make_candidate_schedule, seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def ga_publication(migrated_engine):
    make_session = sessionmaker(bind=migrated_engine, autoflush=False)
    user_id = uuid4()
    with make_session() as session:
        seed_scheduling_problem(session)
        session.add(AppUser(
            user_id=user_id, username=f"ga-publication-{user_id}",
            password_hash="$argon2id$test-only-placeholder",
            role=UserRole.SCHEDULING_COMMITTEE,
        ))
        session.flush()
        repository = JobRepository(session)
        queued = repository.enqueue(
            job_type=JobType.GA, requested_by=user_id, request={},
            timeout_seconds=120, max_attempts=2,
        )
        claim = repository.claim_next(
            claimed_by="ga-publication-test", lease_seconds=300,
            job_types=[JobType.GA],
        )
        assert claim is not None and claim.job_id == queued.job_id
        session.commit()
    candidate = replace(
        make_candidate_schedule(), job_id=claim.job_id,
        assignments=tuple(
            replace(item, meeting_days="M")
            for item in make_candidate_schedule().assignments
        ),
    )
    try:
        yield make_session, claim, candidate
    finally:
        with make_session() as session, session.begin():
            session.execute(delete(Schedule).where(Schedule.term_id == TERM_ID))
            session.execute(delete(ExecutionJob).where(ExecutionJob.job_id == claim.job_id))
            session.execute(delete(AppUser).where(AppUser.user_id == user_id))
            for model in (BlockSectionOffering, BlockSection, SubjectOffering, TimeSlot):
                session.execute(delete(model).where(model.term_id == TERM_ID))
            session.execute(delete(Faculty).where(Faculty.full_name == "<FACULTY-1>"))
            session.execute(delete(Room).where(Room.room_code == "<ROOM-1>"))
            session.execute(delete(Subject).where(Subject.subject_code == "<SUBJECT-1>"))
            session.execute(delete(CourseProgram).where(CourseProgram.program_code == "<PROGRAM-1>"))
            session.execute(delete(Term).where(Term.term_id == TERM_ID))


def _expire(session, job_id):
    session.execute(text(
        "UPDATE execution_job SET lease_expires_at = "
        "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
    ), {"job_id": job_id})


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("invalid", ["expired", "wrong_token", "reclaimed", "timed_out", "wrong_kind", "missing_token"])
def test_invalid_claim_cannot_publish_or_recover(ga_publication, existing, invalid):
    make_session, claim, candidate = ga_publication
    with make_session() as session:
        if existing:
            persist_candidate(session, candidate, claim_token=claim.claim_token)
        repository = JobRepository(session)
        if invalid in ("expired", "reclaimed"):
            _expire(session, claim.job_id)
            if invalid == "reclaimed":
                assert repository.recover_stale().requeued == 1
                replacement = repository.claim_next(
                    claimed_by="replacement", lease_seconds=300, job_types=[JobType.GA],
                )
                assert replacement.claim_token != claim.claim_token
        elif invalid == "timed_out":
            assert repository.timeout(claim.job_id, claim.claim_token) is not None
        elif invalid == "wrong_kind":
            session.execute(text(
                "UPDATE execution_job SET job_type = 'arm' WHERE job_id = :job_id"
            ), {"job_id": claim.job_id})
        session.commit()

    with make_session() as session:
        def refuse_schedule_lookup(state):
            if state.is_select:
                assert not any(
                    table.is_derived_from(Schedule.__table__)
                    for table in state.statement.get_final_froms()
                ), "invalid claim reached schedule lookup"

        event.listen(session, "do_orm_execute", refuse_schedule_lookup)
        token = uuid4() if invalid == "wrong_token" else claim.claim_token
        if invalid == "missing_token":
            token = None
        with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
            persist_candidate(session, candidate, claim_token=token)

    with make_session() as session:
        assert session.scalar(select(func.count()).select_from(Schedule)) == int(existing)
        assert session.scalar(select(func.count()).select_from(ScheduleRecord)) == int(existing)


def test_reclaimed_attempt_recovers_original_schedule_without_overwrite(ga_publication):
    make_session, claim, candidate = ga_publication
    with make_session() as session:
        schedule_id = persist_candidate(session, candidate, claim_token=claim.claim_token).schedule_id
        original_records = session.execute(select(ScheduleRecord.__table__)).all()
        _expire(session, claim.job_id)
        repository = JobRepository(session)
        assert repository.recover_stale().requeued == 1
        retry = repository.claim_next(
            claimed_by="replacement", lease_seconds=300, job_types=[JobType.GA],
        )
        session.commit()
    with make_session() as session:
        changed = CandidateSchedule(term_id=candidate.term_id, assignments=(), job_id=claim.job_id)
        assert persist_candidate(session, changed, claim_token=retry.claim_token).schedule_id == schedule_id
        assert session.execute(select(ScheduleRecord.__table__)).all() == original_records
        assert session.scalar(select(func.count()).select_from(Schedule)) == 1


def test_expiry_after_records_flush_rolls_back_schedule_and_records(ga_publication):
    make_session, claim, candidate = ga_publication
    expired = []
    with make_session() as session:
        def expire_after_records_flush(session, _context):
            if any(isinstance(item, ScheduleRecord) for item in session.identity_map.values()) and not expired:
                expired.append(True)
                _expire(session, claim.job_id)

        event.listen(session, "after_flush_postexec", expire_after_records_flush)
        with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
            persist_candidate(session, candidate, claim_token=claim.claim_token)
        session.rollback()
    assert expired
    with make_session() as session:
        assert session.scalar(select(func.count()).select_from(Schedule)) == 0
        assert session.scalar(select(func.count()).select_from(ScheduleRecord)) == 0


def test_publication_renews_the_lease_before_writing(ga_publication, monkeypatch):
    make_session, claim, candidate = ga_publication
    monkeypatch.setattr(settings, "job_lease_seconds", 300)
    renewed = []
    with make_session() as session:
        session.execute(text(
            "UPDATE execution_job SET lease_expires_at = "
            "clock_timestamp() + INTERVAL '30 seconds' WHERE job_id = :job_id"
        ), {"job_id": claim.job_id})
        previous_deadline = session.scalar(
            select(ExecutionJob.lease_expires_at).where(ExecutionJob.job_id == claim.job_id)
        )
        session.commit()

        def check_renewal_before_insert(session, *_args):
            if any(isinstance(item, Schedule) for item in session.new):
                deadline = session.scalar(select(ExecutionJob.lease_expires_at).where(
                    ExecutionJob.job_id == claim.job_id,
                ))
                assert deadline > previous_deadline
                renewed.append(deadline)

        event.listen(session, "before_flush", check_renewal_before_insert)
        persist_candidate(session, candidate, claim_token=claim.claim_token)
    assert len(renewed) == 1


@pytest.mark.parametrize("expire_waiter", [False, True])
def test_competing_publication_waits_and_rechecks_claim(ga_publication, expire_waiter):
    make_session, claim, candidate = ga_publication
    ready = Event()
    contender_pids = []

    def compete():
        with make_session() as session:
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            contender_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            return persist_candidate(session, candidate, claim_token=claim.claim_token).schedule_id

    with make_session() as owner:
        assert JobRepository(owner).lock_live_claim(claim.job_id, claim.claim_token)
        owner_pid = owner.scalar(text("SELECT pg_backend_pid()"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(compete)
            try:
                assert ready.wait(5)
                deadline = monotonic() + 5
                with make_session() as observer:
                    while owner_pid not in observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": contender_pids[0]},
                    ):
                        if future.done():
                            future.result()
                            pytest.fail("publication did not wait for claim lock")
                        assert monotonic() < deadline
                        sleep(0.01)
                if expire_waiter:
                    _expire(owner, claim.job_id)
                    owner.commit()
                    with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
                        future.result(timeout=10)
                else:
                    schedule_id = persist_candidate(owner, candidate, claim_token=claim.claim_token).schedule_id
                    assert future.result(timeout=10) == schedule_id
            finally:
                owner.rollback()
    with make_session() as session:
        assert session.scalar(select(func.count()).select_from(Schedule)) == int(not expire_waiter)
        assert session.scalar(select(func.count()).select_from(ScheduleRecord)) == int(not expire_waiter)


def test_worker_restart_recovers_interrupted_job_without_duplicating(ga_publication, monkeypatch):
    """CHR-178 / CHR-ENV-005: a worker killed after publishing, before its
    terminal update, is recovered by the next worker loop through lease expiry;
    the reclaimed attempt reuses the schedule and the dead claim stays fenced."""
    from app import worker
    from app.services.jobs.supervisor import JobExecutionResult, JobOutcome

    make_session, claim, candidate = ga_publication
    records = select(ScheduleRecord.__table__).order_by(ScheduleRecord.record_id)
    with make_session() as session:
        # worker.run claims any queued job, so this one must be the only one.
        assert not session.scalar(select(func.count()).select_from(ExecutionJob).where(
            ExecutionJob.job_id != claim.job_id,
            ExecutionJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        ))
        schedule_id = persist_candidate(session, candidate, claim_token=claim.claim_token).schedule_id
        original_records = session.execute(records).all()
        _expire(session, claim.job_id)  # the worker died and its lease ran out
        session.commit()

    stop = Event()
    attempts, late_completions = [], []

    def supervisor(kind, payload, **_kwargs):
        attempts.append(UUID(payload["claim_token"]))
        # While the reclaimed attempt holds a live claim, the dead one is fenced.
        with make_session() as session:
            with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
                persist_candidate(session, candidate, claim_token=claim.claim_token)
        with make_session() as session, session.begin():
            late_completions.append(JobRepository(session).complete(
                claim.job_id, claim.claim_token, result={"schedule_id": "stale"},
            ))
        with make_session() as session:
            published = persist_candidate(session, candidate, claim_token=attempts[-1])
        stop.set()
        return JobExecutionResult(JobOutcome.SUCCESS, {"schedule_id": str(published.schedule_id)})

    monkeypatch.setattr(worker, "SessionLocal", make_session)
    monkeypatch.setattr(worker, "_supervisor", lambda: supervisor)
    guard = Timer(10, stop.set)  # never hang the suite if the job is not reclaimed
    guard.start()
    try:
        worker.run(stop)
    finally:
        guard.cancel()

    assert len(attempts) == 1 and attempts[0] != claim.claim_token
    assert late_completions == [None]
    with make_session() as session:
        job = JobRepository(session).get(claim.job_id)
        assert job.status is JobStatus.SUCCEEDED and job.attempt_count == 2
        assert job.claim_token is None and job.result["schedule_id"] == str(schedule_id)
        assert session.scalar(select(func.count()).select_from(Schedule)) == 1
        assert session.execute(records).all() == original_records
