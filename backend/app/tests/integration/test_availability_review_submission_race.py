"""CHR-132 / CHR-235: a GA job freezes the masks readiness checked.

Two committed transactions are needed: a savepoint inside one connection
cannot show what a second session's availability save does to a submission
that is reading masks. Every test removes the rows it committed.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.errors import APIError
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    Room,
    Schedule,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)
from app.repositories.scheduling import (
    SQLAlchemyBlockGenerationContextProvider,
    SQLAlchemySchedulingProblemProvider,
)
from app.schemas.jobs import SubmitGAJobRequest
from app.services.catalog.availability import set_availability
from app.services.catalog import availability as availability_service
from app.services.catalog.terms import select_term
from app.services.job_submission import JobSubmissionService
from app.services.jobs.codecs import (
    encode_arm_payload,
    encode_block_ga_payload,
    encode_ga_payload,
)
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    OFFERING_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres
OTHER_TERM_ID = UUID("16900000-0000-0000-0000-000000000001")


class _Enqueued(Exception):
    pass


@pytest.fixture
def committed_problem(migrated_engine):
    """The seeded problem, committed, with its one mask ``1`` unreviewed."""
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        seed_scheduling_problem(session)
        session.get(Faculty, FACULTY_ID).availability_reviewed_term_id = None
        session.add(Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID))
        session.add(Term(
            term_id=OTHER_TERM_ID, academic_year="2027-2028", term_number=1,
            is_active=False,
        ))
        session.commit()
    try:
        yield make_session
    finally:
        with make_session() as session:
            for statement in (
                delete(BlockSectionOffering).where(BlockSectionOffering.class_id == CLASS_ID),
                delete(TimeSlot).where(TimeSlot.time_slot_id == TIME_SLOT_ID),
                delete(SubjectOffering).where(SubjectOffering.offering_id == OFFERING_ID),
                delete(BlockSection).where(BlockSection.block_id == BLOCK_ID),
                delete(Schedule).where(Schedule.schedule_id == SCHEDULE_ID),
                delete(Faculty).where(Faculty.faculty_id == FACULTY_ID),
                delete(CourseProgram).where(CourseProgram.program_code == "<PROGRAM-1>"),
                delete(Room).where(Room.room_code == "<ROOM-1>"),
                delete(Subject).where(Subject.subject_code == "<SUBJECT-1>"),
                delete(Term).where(Term.term_id == TERM_ID),
                delete(Term).where(Term.term_id == OTHER_TERM_ID),
            ):
                session.execute(statement)
            session.commit()


@pytest.mark.parametrize("per_block", [False, True])
def test_a_save_after_masks_are_captured_waits_for_the_submission(
    committed_problem, monkeypatch, per_block: bool,
):
    """Another session saves ``0`` right after the submission has read ``1``.
    The save must wait for the submission, so the job queues the ``1`` that
    readiness checked. Review state no longer gates submission (CHR-235)."""
    make_session = committed_problem
    save_outcome: list[str] = []

    def save_replacement_mask() -> None:
        with make_session() as other:
            other.execute(text("SET lock_timeout = '500ms'"))
            try:
                set_availability(other, FACULTY_ID, TERM_ID, [])
                save_outcome.append("saved")
            except OperationalError:
                save_outcome.append("waited")

    provider, method = (
        (SQLAlchemyBlockGenerationContextProvider, "get_block_generation_context")
        if per_block
        else (SQLAlchemySchedulingProblemProvider, "get_problem")
    )
    original = getattr(provider, method)

    def read_then_interleave(self, *args, **kwargs):
        captured = original(self, *args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(save_replacement_mask).result()
        return captured

    monkeypatch.setattr(provider, method, read_then_interleave)
    repository = MagicMock()
    # Stop at enqueue: the point is what reaches it, not storing a job.
    repository.enqueue.side_effect = _Enqueued
    request = SubmitGAJobRequest(term_id=TERM_ID)
    if per_block:
        request = SubmitGAJobRequest(
            term_id=TERM_ID, block_id=BLOCK_ID, base_schedule_id=SCHEDULE_ID
        )

    with make_session() as session:
        service = JobSubmissionService(
            session,
            repository,
            encode_ga_payload,
            encode_arm_payload,
            encode_block_ga_payload,
            max_attempts=1,
            ga_timeout_seconds=60,
            arm_timeout_seconds=60,
        )
        with pytest.raises(_Enqueued):
            service.submit_ga(request, FACULTY_ID)
        session.rollback()

    assert save_outcome == ["waited"]
    repository.enqueue.assert_called_once()


def test_term_switch_waits_until_availability_save_commits(committed_problem, monkeypatch):
    make_session = committed_problem
    outcomes = []
    original_encode = availability_service._codec.encode

    def switch_term():
        with make_session() as other:
            other.execute(text("SET LOCAL lock_timeout = '500ms'"))
            try:
                select_term(other, OTHER_TERM_ID)
                outcomes.append("switched")
            except OperationalError:
                outcomes.append("waited")

    def encode_during_switch(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(switch_term).result(timeout=5)
        return original_encode(*args, **kwargs)

    monkeypatch.setattr(availability_service._codec, "encode", encode_during_switch)
    with make_session() as session:
        set_availability(session, FACULTY_ID, TERM_ID, [])
    assert outcomes == ["waited"]
    with make_session() as session:
        assert session.get(Term, TERM_ID).is_active is True
        faculty = session.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "0"
        assert faculty.availability_reviewed_term_id == TERM_ID


def test_save_rechecks_active_term_after_waiting_for_a_switch(committed_problem):
    make_session = committed_problem
    ready = Event()
    contender_pids = []

    def save():
        with make_session() as session:
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            contender_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            set_availability(session, FACULTY_ID, TERM_ID, [])

    with make_session() as switching:
        owner_pid = switching.scalar(text("SELECT pg_backend_pid()"))
        switching.get(Term, TERM_ID).is_active = False
        switching.flush()
        switching.get(Term, OTHER_TERM_ID).is_active = True
        switching.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(save)
            try:
                assert ready.wait(5)
                deadline = monotonic() + 5
                with make_session() as observer:
                    while owner_pid not in observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": contender_pids[0]},
                    ):
                        if future.done():
                            future.result()
                            pytest.fail("save did not wait for the term switch")
                        assert monotonic() < deadline
                        sleep(0.01)
                switching.commit()
                with pytest.raises(APIError) as refused:
                    future.result(timeout=10)
                assert refused.value.status_code == 409
                assert refused.value.code == "AVAILABILITY_REVIEW_STALE"
            finally:
                switching.rollback()
    with make_session() as session:
        faculty = session.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "1"
        assert faculty.availability_reviewed_term_id is None


def test_a_deactivation_waits_for_an_assignment_edit_that_read_the_teacher(
    committed_problem, monkeypatch,
):
    """CHR-239: an edit that checked the teacher is active keeps that true
    until it commits, so a concurrent "not currently teaching" waits."""
    from app.models import ScheduleRecord
    from app.schemas.schedule import AssignmentUpdate
    from app.services.schedules import update_assignment

    make_session = committed_problem
    with make_session() as session:
        record = ScheduleRecord(
            schedule_id=SCHEDULE_ID, term_id=TERM_ID, class_id=CLASS_ID,
            subject_code="<SUBJECT-1>", faculty_id=FACULTY_ID,
            time_slot_id=TIME_SLOT_ID, time_slot="<TIME-SLOT-1>",
            room_code="<ROOM-1>", meeting_days="M", is_historical=False,
        )
        session.add(record)
        session.commit()
        record_id = record.record_id
    outcome: list[str] = []

    def deactivate() -> None:
        with make_session() as other:
            other.execute(text("SET lock_timeout = '500ms'"))
            try:
                other.get(Faculty, FACULTY_ID).is_active = False
                other.commit()
                outcome.append("deactivated")
            except OperationalError:
                outcome.append("waited")

    original = SQLAlchemySchedulingProblemProvider.get_problem

    def read_then_interleave(self, *args, **kwargs):
        captured = original(self, *args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(deactivate).result()
        return captured

    monkeypatch.setattr(SQLAlchemySchedulingProblemProvider, "get_problem", read_then_interleave)

    try:
        with make_session() as session:
            update_assignment(
                session, SCHEDULE_ID, record_id,
                AssignmentUpdate(faculty_id=FACULTY_ID, meeting_days="MW"),
            )
    finally:
        with make_session() as session:
            session.execute(delete(ScheduleRecord).where(ScheduleRecord.record_id == record_id))
            session.commit()

    assert outcome == ["waited"]


def test_a_deactivation_waits_for_a_term_wide_publication_that_read_the_teacher(
    committed_problem, monkeypatch,
):
    """CHR-239: term-wide GA publication keeps "teacher is active" true until
    its records commit, so a concurrent deactivation waits."""
    from app.domain import Assignment, CandidateSchedule
    from app.services.schedules import persist_candidate

    make_session = committed_problem
    published = UUID("16900000-0000-0000-0000-000000000099")
    outcome: list[str] = []

    def deactivate() -> None:
        with make_session() as other:
            other.execute(text("SET lock_timeout = '500ms'"))
            try:
                other.get(Faculty, FACULTY_ID).is_active = False
                other.commit()
                outcome.append("deactivated")
            except OperationalError:
                outcome.append("waited")

    original = SQLAlchemySchedulingProblemProvider.get_problem

    def read_then_interleave(self, *args, **kwargs):
        captured = original(self, *args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(deactivate).result()
        return captured

    monkeypatch.setattr(SQLAlchemySchedulingProblemProvider, "get_problem", read_then_interleave)

    try:
        with make_session() as session:
            persist_candidate(
                session,
                CandidateSchedule(
                    schedule_id=published,
                    term_id=TERM_ID,
                    assignments=(
                        Assignment(
                            class_id=CLASS_ID, faculty_id=FACULTY_ID,
                            time_slot_id=TIME_SLOT_ID, room_code="<ROOM-1>",
                            meeting_days="M",
                        ),
                    ),
                ),
            )
    finally:
        with make_session() as session:
            session.execute(delete(Schedule).where(Schedule.schedule_id == published))
            session.commit()

    assert outcome == ["waited"]
