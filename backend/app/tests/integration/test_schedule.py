"""PostgreSQL guarantees for CHR-22 / CHR-DB-003 schedule provenance."""

from datetime import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    Schedule,
    ScheduleRecord,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)

pytestmark = pytest.mark.postgres


def make_problem_data(session: Session, suffix: str = ""):
    term = Term(
        academic_year="2026-2027" if suffix else "2025-2026",
        term_number=1,
        is_active=not suffix,
    )
    subject = Subject(
        subject_code=f"CS30{suffix or '0'}",
        subject_title="<SUBJECT>",
        units=3,
        subject_type="LEC",
    )
    program = CourseProgram(program_code=f"BSCS{suffix}", program_name="<PROGRAM>")
    faculty = Faculty(full_name="<FACULTY>", is_full_time=False, availability_mask="1")
    session.add_all([term, subject, program, faculty])
    session.flush()

    block = BlockSection(
        term_id=term.term_id,
        program_code=program.program_code,
        year_level=1,
        section_letter="A",
    )
    offering = SubjectOffering(
        term_id=term.term_id, subject_code=subject.subject_code, group_no=1
    )
    slot = TimeSlot(
        term_id=term.term_id,
        label="08:00-09:00",
        start_time=time(8),
        end_time=time(9),
        ordinal=0,
    )
    schedule = Schedule(term_id=term.term_id)
    session.add_all([block, offering, slot, schedule])
    session.flush()
    block_class = BlockSectionOffering(
        block_id=block.block_id,
        offering_id=offering.offering_id,
        term_id=term.term_id,
        subject_code=subject.subject_code,
    )
    session.add(block_class)
    session.flush()
    return term, subject, program, faculty, block_class, slot, schedule


def make_assignment(problem_data, **overrides) -> ScheduleRecord:
    term, subject, _, faculty, block_class, slot, schedule = problem_data
    values = {
        "faculty_id": faculty.faculty_id,
        "subject_code": subject.subject_code,
        "term_id": term.term_id,
        "schedule_id": schedule.schedule_id,
        "class_id": block_class.class_id,
        "time_slot_id": slot.time_slot_id,
        "meeting_days": "MWF",
        "time_slot": slot.label,
        "is_historical": False,
    }
    values.update(overrides)
    return ScheduleRecord(**values)


def test_schedule_assignment_persists_complete_provenance(db_session: Session):
    problem_data = make_problem_data(db_session)
    assignment = make_assignment(problem_data)
    db_session.add(assignment)
    db_session.flush()

    _, _, _, _, block_class, slot, schedule = problem_data
    assert assignment.schedule is schedule
    assert assignment.block_class is block_class
    assert assignment.canonical_time_slot is slot
    assert schedule.records == [assignment]


def test_term_rejects_a_second_master_schedule(db_session: Session):
    problem_data = make_problem_data(db_session)
    term = problem_data[0]
    db_session.add_all(
        [
            Schedule(term_id=term.term_id, schedule_type="master"),
            Schedule(term_id=term.term_id, schedule_type="master"),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_schedule_one_master_per_term"):
        db_session.flush()


def test_schedule_may_leave_a_required_class_unassigned(db_session: Session):
    problem_data = make_problem_data(db_session)
    term, subject, program, _, _, _, schedule = problem_data
    unscheduled_block = BlockSection(
        term_id=term.term_id,
        program_code=program.program_code,
        year_level=1,
        section_letter="B",
    )
    db_session.add(unscheduled_block)
    db_session.flush()
    db_session.add(
        BlockSectionOffering(
            block_id=unscheduled_block.block_id,
            offering_id=db_session.query(SubjectOffering)
            .filter_by(term_id=term.term_id)
            .one()
            .offering_id,
            term_id=term.term_id,
            subject_code=subject.subject_code,
        )
    )
    db_session.add(make_assignment(problem_data))
    db_session.flush()

    required_count = db_session.scalar(
        select(func.count())
        .select_from(BlockSectionOffering)
        .where(BlockSectionOffering.term_id == term.term_id)
    )
    assigned_count = db_session.scalar(
        select(func.count())
        .select_from(ScheduleRecord)
        .where(ScheduleRecord.schedule_id == schedule.schedule_id)
    )

    assert required_count == 2
    assert assigned_count == 1


def test_duplicate_class_assignment_in_one_schedule_is_rejected(
    db_session: Session,
):
    problem_data = make_problem_data(db_session)
    db_session.add(make_assignment(problem_data))
    db_session.flush()
    db_session.add(make_assignment(problem_data))

    with pytest.raises(IntegrityError, match="uq_schedule_record_schedule_class"):
        db_session.flush()


@pytest.mark.parametrize("missing_field", ["schedule_id", "class_id", "time_slot_id"])
def test_partial_assignment_provenance_is_rejected(
    db_session: Session, missing_field: str
):
    problem_data = make_problem_data(db_session)
    db_session.add(make_assignment(problem_data, **{missing_field: None}))

    with pytest.raises(
        IntegrityError, match="ck_schedule_record_assignment_provenance"
    ):
        db_session.flush()


def test_assignment_rejects_class_from_another_term(db_session: Session):
    problem_data = make_problem_data(db_session)
    other_data = make_problem_data(db_session, suffix="B")
    other_section = other_data[4]
    db_session.add(make_assignment(problem_data, class_id=other_section.class_id))

    with pytest.raises(IntegrityError, match="fk_schedule_record_class_term_subject"):
        db_session.flush()


def test_unlinked_legacy_record_remains_valid(db_session: Session):
    term, subject, _, faculty, _, _, _ = make_problem_data(db_session)
    legacy_record = ScheduleRecord(
        faculty_id=faculty.faculty_id,
        subject_code=subject.subject_code,
        term_id=term.term_id,
        meeting_days="MWF",
        time_slot="08:00-09:00",
        is_historical=True,
    )
    db_session.add(legacy_record)
    db_session.flush()

    assert legacy_record.schedule_id is None
    assert legacy_record.class_id is None
    assert legacy_record.time_slot_id is None


def test_deleting_schedule_removes_its_assignments(db_session: Session):
    problem_data = make_problem_data(db_session)
    schedule = problem_data[6]
    db_session.add(make_assignment(problem_data))
    db_session.flush()

    db_session.delete(schedule)
    db_session.flush()

    remaining = db_session.scalar(
        select(func.count())
        .select_from(ScheduleRecord)
        .where(ScheduleRecord.schedule_id == schedule.schedule_id)
    )
    assert remaining == 0


def test_deleting_schedule_keeps_unlinked_legacy_records(db_session: Session):
    problem_data = make_problem_data(db_session)
    term, subject, _, faculty, _, _, schedule = problem_data
    legacy_record = ScheduleRecord(
        faculty_id=faculty.faculty_id,
        subject_code=subject.subject_code,
        term_id=term.term_id,
        meeting_days="MWF",
        time_slot="08:00-09:00",
        is_historical=True,
    )
    db_session.add_all([legacy_record, make_assignment(problem_data)])
    db_session.flush()

    db_session.delete(schedule)
    db_session.flush()

    assert db_session.get(ScheduleRecord, legacy_record.record_id) is not None


def test_deleting_an_assigned_class_is_rejected(db_session: Session):
    problem_data = make_problem_data(db_session)
    db_session.add(make_assignment(problem_data))
    db_session.flush()
    db_session.delete(problem_data[4])

    with pytest.raises(IntegrityError, match="fk_schedule_record_class_term_subject"):
        db_session.flush()


def test_deleting_an_assigned_time_slot_is_rejected(db_session: Session):
    problem_data = make_problem_data(db_session)
    db_session.add(make_assignment(problem_data))
    db_session.flush()
    db_session.delete(problem_data[5])

    with pytest.raises(IntegrityError, match="fk_schedule_record_time_slot_term"):
        db_session.flush()
