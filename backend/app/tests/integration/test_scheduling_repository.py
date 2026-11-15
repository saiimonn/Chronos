from dataclasses import replace
from datetime import time
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    CourseProgram,
    Faculty,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)
from app.repositories import SchedulingProblemNotFoundError
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.tests.support.scheduling import (
    FixtureSchedulingProblemProvider,
    TERM_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres


def test_database_and_fixture_providers_yield_equivalent_domain_data(
    db_session: Session,
):
    seed_scheduling_problem(db_session)

    database_problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(
        TERM_ID
    )
    fixture_problem = FixtureSchedulingProblemProvider().get_problem(TERM_ID)

    assert replace(database_problem, faculty=fixture_problem.faculty) == fixture_problem
    assert fixture_problem.faculty[0] in database_problem.faculty
    db_session.expunge_all()
    assert database_problem.classes[0].subject_code == "<SUBJECT-1>"


def test_database_provider_scopes_term_data_and_orders_slots(db_session: Session):
    seed_scheduling_problem(db_session)
    other_term_id = UUID("00000000-0000-0000-0000-000000000010")
    other_block_id = UUID("00000000-0000-0000-0000-000000000011")
    other_offering_id = UUID("00000000-0000-0000-0000-000000000014")
    other_slot_id = UUID("00000000-0000-0000-0000-000000000012")
    second_slot_id = UUID("00000000-0000-0000-0000-000000000013")
    db_session.add_all(
        [
            Term(
                term_id=other_term_id,
                academic_year="<YEAR-2>",
                term_number=2,
                is_active=False,
            ),
            Subject(
                subject_code="<OTHER-SUBJECT>",
                subject_title="<OTHER-TITLE>",
                units=3,
                subject_type="LEC",
            ),
            CourseProgram(
                program_code="<OTHER-PROGRAM>",
                program_name="<OTHER-PROGRAM-NAME>",
            ),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            BlockSection(
                block_id=other_block_id,
                term_id=other_term_id,
                program_code="<OTHER-PROGRAM>",
                year_level=1,
                section_letter="A",
            ),
            SubjectOffering(
                offering_id=other_offering_id,
                term_id=other_term_id,
                subject_code="<OTHER-SUBJECT>",
                group_no=1,
            ),
            TimeSlot(
                time_slot_id=other_slot_id,
                term_id=other_term_id,
                label="<OTHER-SLOT>",
                start_time=time(10),
                end_time=time(11),
                ordinal=0,
            ),
            TimeSlot(
                time_slot_id=second_slot_id,
                term_id=TERM_ID,
                label="<TIME-SLOT-2>",
                start_time=time(9),
                end_time=time(10),
                ordinal=1,
            ),
        ]
    )
    db_session.flush()

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)

    assert [section.class_id for section in problem.classes] == [
        UUID("00000000-0000-0000-0000-000000000003")
    ]
    assert [slot.ordinal for slot in problem.time_slots] == [0, 1]
    assert [subject.subject_code for subject in problem.subjects] == ["<SUBJECT-1>"]


def test_database_provider_rejects_unknown_term(db_session: Session):
    unknown_term_id = UUID("00000000-0000-0000-0000-000000000099")

    with pytest.raises(SchedulingProblemNotFoundError) as exc_info:
        SQLAlchemySchedulingProblemProvider(db_session).get_problem(unknown_term_id)

    assert exc_info.value.term_id == unknown_term_id


def test_database_provider_includes_all_faculty(db_session: Session):
    seed_scheduling_problem(db_session)
    extra_faculty_id = UUID("00000000-0000-0000-0000-000000000099")
    db_session.add(
        Faculty(
            faculty_id=extra_faculty_id,
            full_name="<EXTRA-FACULTY>",
            is_full_time=False,
            availability_mask="1",
        )
    )
    db_session.flush()

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)

    faculty_ids = [faculty.faculty_id for faculty in problem.faculty]
    assert faculty_ids == sorted(faculty_ids, key=str)
    assert UUID("00000000-0000-0000-0000-000000000002") in faculty_ids
    assert extra_faculty_id in faculty_ids


def test_database_provider_flags_inactive_faculty(db_session: Session):
    seed_scheduling_problem(db_session)
    retired_id = UUID("00000000-0000-0000-0000-000000000098")
    # An unset mask reads as open in every slot, so without the flag this
    # retired instructor would be a scheduling candidate.
    db_session.add(
        Faculty(
            faculty_id=retired_id,
            full_name="<RETIRED-FACULTY>",
            is_full_time=False,
            is_active=False,
            availability_mask="",
        )
    )
    db_session.flush()

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)

    # Loaded, so schedules naming them still resolve, but flagged so the GA
    # and readiness pass over them.
    status = {faculty.faculty_id: faculty.is_active for faculty in problem.faculty}
    assert status[retired_id] is False
    assert status[UUID("00000000-0000-0000-0000-000000000002")] is True
