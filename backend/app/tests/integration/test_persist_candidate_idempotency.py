"""CHR-78 CHR-SCH-004 persist_candidate idempotency against real Postgres."""

from datetime import time
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.scheduling import Assignment, CandidateSchedule
from app.models import (
    AppUser,
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    ExecutionJob,
    Faculty,
    JobType,
    Room,
    Schedule,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
    UserRole,
)
from app.services.schedules import persist_candidate
from app.repositories.jobs import JobRepository

pytestmark = pytest.mark.postgres

_JOB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _build_minimal_catalog(session: Session):
    """Insert the minimum rows needed by SQLAlchemySchedulingProblemProvider."""
    term = Term(academic_year="2025-2026", term_number=1, is_active=True)
    subject = Subject(
        subject_code="CS101",
        subject_title="<SUBJECT>",
        units=3,
        subject_type="LEC",
    )
    program = CourseProgram(program_code="BSCS", program_name="<PROGRAM>")
    faculty = Faculty(full_name="<FACULTY>", is_full_time=True, availability_mask="1")
    room = Room(
        room_code="R101",
        building_name="<BUILDING>",
        capacity=40,
        room_type="lecture",
    )
    user = AppUser(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        username="idempotency-test-user",
        password_hash="$argon2id$test-only-placeholder",
        role=UserRole.SCHEDULING_COMMITTEE,
    )
    session.add_all([term, subject, program, faculty, room, user])
    session.flush()

    job = ExecutionJob(
        job_id=_JOB_ID,
        job_type=JobType.GA,
        requested_by=user.user_id,
        request={"term_id": str(term.term_id)},
        timeout_seconds=120,
        max_attempts=1,
        status="queued",
    )
    session.add(job)
    session.flush()

    block = BlockSection(
        term_id=term.term_id,
        program_code=program.program_code,
        year_level=1,
        section_letter="A",
    )
    offering = SubjectOffering(
        term_id=term.term_id,
        subject_code=subject.subject_code,
        group_no=1,
    )
    slot = TimeSlot(
        term_id=term.term_id,
        label="08:00-09:00",
        start_time=time(8),
        end_time=time(9),
        ordinal=0,
    )
    session.add_all([block, offering, slot])
    session.flush()

    block_class = BlockSectionOffering(
        block_id=block.block_id,
        offering_id=offering.offering_id,
        term_id=term.term_id,
        subject_code=subject.subject_code,
    )
    session.add(block_class)
    session.flush()

    return term, faculty, slot, block_class.class_id, room.room_code


def _make_candidate(term, faculty, slot, class_id, room_code, job_id=None):
    return CandidateSchedule(
        term_id=term.term_id,
        assignments=(
            Assignment(
                class_id=class_id,
                faculty_id=faculty.faculty_id,
                time_slot_id=slot.time_slot_id,
                room_code=room_code,
                meeting_days="MWF",
            ),
        ),
        job_id=job_id,
    )


def test_persist_candidate_idempotent_on_job_id(db_session: Session):
    """Calling persist_candidate twice with the same job_id yields one row."""
    term, faculty, slot, class_id, room_code = _build_minimal_catalog(db_session)
    claim = JobRepository(db_session).claim_next(
        claimed_by="idempotency-test", lease_seconds=300, job_types=[JobType.GA],
    )
    assert claim is not None and claim.job_id == _JOB_ID

    first = _make_candidate(term, faculty, slot, class_id, room_code, job_id=_JOB_ID)
    schedule_a = persist_candidate(db_session, first, claim_token=claim.claim_token)

    second = _make_candidate(term, faculty, slot, class_id, room_code, job_id=_JOB_ID)
    schedule_b = persist_candidate(db_session, second, claim_token=claim.claim_token)

    assert schedule_a.schedule_id == schedule_b.schedule_id

    count = db_session.scalar(
        select(func.count()).select_from(Schedule).where(Schedule.job_id == _JOB_ID)
    )
    assert count == 1
