"""Finalization racing a manual edit under real concurrent PostgreSQL
transactions. Commits for real, so it cleans up every row it wrote."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.domain import Assignment, CandidateSchedule
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    Room,
    Schedule,
    ScheduleRecord,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)
from app.schemas.schedule import AssignmentUpdate
from app.services import schedules as service
from app.tests.support.scheduling import (
    CLASS_ID,
    FACULTY_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

SMALL_ROOM = "<ROOM-2>"


@pytest.fixture
def committed_valid_schedule(migrated_engine):
    make_session = sessionmaker(bind=migrated_engine)
    problem = make_scheduling_problem()
    with make_session() as session:
        seed_scheduling_problem(session)
        # Too small for the class: moving there is a hard conflict.
        session.add(
            Room(
                room_code=SMALL_ROOM,
                building_name="<BUILDING-1>",
                capacity=problem.classes[0].capacity - 1,
                room_type=problem.rooms[0].room_type.value,
                expanded=False,
            )
        )
        session.flush()
        service.persist_candidate(
            session,
            CandidateSchedule(
                schedule_id=SCHEDULE_ID,
                term_id=TERM_ID,
                assignments=(
                    Assignment(
                        class_id=CLASS_ID,
                        faculty_id=FACULTY_ID,
                        time_slot_id=TIME_SLOT_ID,
                        room_code="<ROOM-1>",
                        meeting_days="MW",
                    ),
                ),
            ),
        )

    yield make_session

    with make_session() as session:
        session.execute(delete(Schedule).where(Schedule.term_id == TERM_ID))
        session.execute(
            delete(BlockSectionOffering).where(BlockSectionOffering.term_id == TERM_ID)
        )
        session.execute(delete(BlockSection).where(BlockSection.term_id == TERM_ID))
        session.execute(
            delete(SubjectOffering).where(SubjectOffering.term_id == TERM_ID)
        )
        session.execute(delete(TimeSlot).where(TimeSlot.term_id == TERM_ID))
        session.execute(
            delete(Room).where(Room.room_code.in_(["<ROOM-1>", SMALL_ROOM]))
        )
        session.execute(
            delete(Subject).where(
                Subject.subject_code == problem.subjects[0].subject_code
            )
        )
        session.execute(delete(Faculty).where(Faculty.faculty_id == FACULTY_ID))
        session.execute(
            delete(CourseProgram).where(
                CourseProgram.program_code == problem.programs[0].program_code
            )
        )
        session.execute(delete(Term).where(Term.term_id == TERM_ID))
        session.commit()


def test_finalize_never_freezes_a_conflict_added_by_a_concurrent_edit(
    committed_valid_schedule, monkeypatch
):
    make_session = committed_valid_schedule
    with make_session() as session:
        record_id = session.scalar(
            select(ScheduleRecord.record_id).where(
                ScheduleRecord.class_id == CLASS_ID
            )
        )

    # Hold each request after it has read the records, so without the
    # Schedule lock finalization validates the pre-edit state.
    both_read = threading.Barrier(2, timeout=2)
    load_problem = service._get_problem

    def get_problem_after_both_read(*args, **kwargs):
        try:
            both_read.wait()
        except threading.BrokenBarrierError:
            pass  # The other request is waiting on the lock: proceed alone.
        return load_problem(*args, **kwargs)

    monkeypatch.setattr(service, "_get_problem", get_problem_after_both_read)

    def finalize():
        with make_session() as session:
            service.finalize_schedule(session, SCHEDULE_ID)

    def edit():
        with make_session() as session:
            service.update_assignment(
                session, SCHEDULE_ID, record_id, AssignmentUpdate(room_code=SMALL_ROOM)
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finalize), pool.submit(edit)]
    refused = sorted(f.exception().code for f in futures if f.exception())

    # Exactly one side loses: the edit is refused as finalized, or finalization
    # is refused because it sees the edit's conflict.
    assert refused in (["SCHEDULE_FINALIZED"], ["SCHEDULE_NOT_EXPORTABLE"])
    with make_session() as session:
        finalized = session.get(Schedule, SCHEDULE_ID).finalized_at is not None
        result = service.validate_schedule(session, SCHEDULE_ID)
    assert finalized == result.is_export_eligible
