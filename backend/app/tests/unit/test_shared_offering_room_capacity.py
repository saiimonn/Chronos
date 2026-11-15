"""CHR-164 / CHR-FIX-004.

A per-Block run sees only the selected Block's classes. When its Offering is
shared with another Block, the room must seat every Block of the Offering,
not just this one.
"""

from app.domain import (
    Assignment,
    BlockGenerationContext,
    BlockSectionDTO,
    CandidateSchedule,
    ConflictCode,
    build_offering_enrollment,
)
from app.services.ga.config import GAConfig
from app.services.ga.operators import GeneSampler
from app.services.jobs.codecs import (
    decode_block_generation_context,
    encode_block_generation_context,
)
from app.services.validation import ScheduleValidator
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    OFFERING_LECTURE_ID,
    SLOT_MORNING_ID,
    make_combined_offering_problem,
)

COMBINED = make_combined_offering_problem()
CLASS_A, CLASS_B = COMBINED.classes


def _context() -> BlockGenerationContext:
    return BlockGenerationContext(
        term=COMBINED.term,
        selected_block_id=CLASS_A.block_id,
        selected_block=BlockSectionDTO(
            block_id=CLASS_A.block_id,
            term_id=COMBINED.term.term_id,
            program_code=CLASS_A.program_code,
            year_level=CLASS_A.year_level,
            section_letter=CLASS_A.section_letter,
            capacity=CLASS_A.capacity,
        ),
        selected_classes=(CLASS_A,),
        locked_assignments=(),
        faculty=COMBINED.faculty,
        subjects=COMBINED.subjects,
        rooms=COMBINED.rooms,
        programs=COMBINED.programs,
        time_slots=COMBINED.time_slots,
        offering_block_ids=COMBINED.offering_block_ids,
        offering_enrollment=build_offering_enrollment(COMBINED.classes),
    )


def test_per_block_room_pool_seats_every_block_of_the_offering():
    # Two 20-student Blocks share the Offering; the 25-seat room fits one only.
    pool = GeneSampler(_context().selected_problem, GAConfig()).room_pool(
        OFFERING_LECTURE_ID
    )

    assert pool == (LECTURE_ROOM,)


def test_per_block_validation_counts_seats_of_blocks_it_does_not_schedule():
    result = ScheduleValidator().validate_block_context(
        _context(),
        CandidateSchedule(
            term_id=COMBINED.term.term_id,
            assignments=(
                Assignment(
                    class_id=CLASS_A.class_id,
                    faculty_id=FACULTY_FLEXIBLE_ID,
                    time_slot_id=SLOT_MORNING_ID,
                    room_code=LABORATORY_ROOM,
                    meeting_days="TTh",
                ),
            ),
        ),
    )

    capacity = [
        conflict
        for conflict in result.constraint_report.conflicts
        if conflict.code is ConflictCode.ROOM_CAPACITY_EXCEEDED
    ]
    assert len(capacity) == 1
    assert "combined Block enrollment 40" in capacity[0].message


def test_offering_enrollment_survives_the_job_payload():
    decoded = decode_block_generation_context(
        encode_block_generation_context(_context())
    )

    assert decoded.offering_enrollment == {OFFERING_LECTURE_ID: 40}


def test_payload_without_enrollment_falls_back_to_the_selected_block():
    payload = encode_block_generation_context(_context())
    del payload["context"]["offering_enrollment"]

    problem = decode_block_generation_context(payload).selected_problem

    assert problem.enrollment_of(OFFERING_LECTURE_ID, CLASS_A.capacity) == 20
