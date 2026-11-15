"""Offering-wide manual edits under real concurrent PostgreSQL transactions.

These tests commit rather than rolling back inside one transaction: the whole
point is what two committed transactions do to each other, which a savepoint
inside a single connection cannot show. Every test cleans up the rows it wrote.
"""

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
    OTHER_CLASS_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    add_combined_block,
    make_scheduling_problem,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

OTHER_ROOM = "<ROOM-2>"


@pytest.fixture
def committed_split_offering(migrated_engine):
    """A committed schedule whose combined Offering is split across two rooms,
    as a schedule saved before shared placement was enforced can be."""
    make_session = sessionmaker(bind=migrated_engine)
    problem = make_scheduling_problem()
    with make_session() as session:
        seed_scheduling_problem(session)
        add_combined_block(session)
        session.add(
            Room(
                room_code=OTHER_ROOM,
                building_name="<BUILDING-1>",
                capacity=40,
                room_type=problem.rooms[0].room_type.value,
                expanded=False,
            )
        )
        session.flush()
        placement = {
            "faculty_id": FACULTY_ID,
            "time_slot_id": TIME_SLOT_ID,
            "meeting_days": "MW",
        }
        service.persist_candidate(
            session,
            CandidateSchedule(
                schedule_id=SCHEDULE_ID,
                term_id=TERM_ID,
                assignments=(
                    Assignment(class_id=CLASS_ID, room_code="<ROOM-1>", **placement),
                    Assignment(
                        class_id=OTHER_CLASS_ID, room_code=OTHER_ROOM, **placement
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
            delete(Room).where(Room.room_code.in_(["<ROOM-1>", OTHER_ROOM]))
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


def test_opposite_repairs_of_a_split_offering_leave_it_whole(
    committed_split_offering, monkeypatch
):
    make_session = committed_split_offering
    with make_session() as session:
        record_id = session.scalar(
            select(ScheduleRecord.record_id).where(
                ScheduleRecord.class_id == CLASS_ID
            )
        )

    # Hold each request after it has read the records, so without the
    # Schedule lock both read the same split state before either writes.
    both_read = threading.Barrier(2, timeout=2)
    load_problem = service._get_problem

    def get_problem_after_both_read(*args, **kwargs):
        try:
            both_read.wait()
        except threading.BrokenBarrierError:
            pass  # The other request is waiting on the lock: proceed alone.
        return load_problem(*args, **kwargs)

    monkeypatch.setattr(service, "_get_problem", get_problem_after_both_read)

    def repair_to(room_code):
        with make_session() as session:
            _, result = service.update_assignment(
                session,
                SCHEDULE_ID,
                record_id,
                AssignmentUpdate(room_code=room_code),
            )
            return result.constraint_report.hard_conflict_count

    with ThreadPoolExecutor(max_workers=2) as pool:
        hard_conflicts = list(pool.map(repair_to, ["<ROOM-1>", OTHER_ROOM]))

    assert hard_conflicts == [0, 0]
    with make_session() as session:
        rooms = session.scalars(
            select(ScheduleRecord.room_code).where(
                ScheduleRecord.schedule_id == SCHEDULE_ID
            )
        ).all()
    assert len(rooms) == 2
    assert len(set(rooms)) == 1
