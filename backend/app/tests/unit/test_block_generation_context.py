"""CHR-89 / CHR-SCH-009 Block generation context domain tests."""

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from app.domain import BlockGenerationContext, BlockSectionDTO, LockedAssignment
from app.tests.support.scheduling import (
    FACULTY_ID,
    TIME_SLOT_ID,
    TERM_ID,
    make_scheduling_problem,
)

BLOCK_ID = UUID("00000000-0000-0000-0000-000000000006")
OTHER_BLOCK_ID = UUID("00000000-0000-0000-0000-000000000020")
CLASS_ID = UUID("00000000-0000-0000-0000-000000000003")
LOCKED_CLASS_ID = UUID("00000000-0000-0000-0000-000000000030")
SCHEDULE_ID = UUID("00000000-0000-0000-0000-000000000005")


def make_locked_assignment() -> LockedAssignment:
    return LockedAssignment(
        class_id=LOCKED_CLASS_ID,
        faculty_id=FACULTY_ID,
        time_slot_id=TIME_SLOT_ID,
        room_code="<ROOM-1>",
        meeting_days="MWF",
        block_id=OTHER_BLOCK_ID,
    )


def make_block_generation_context() -> BlockGenerationContext:
    problem = make_scheduling_problem()
    selected_block = BlockSectionDTO(
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="A",
        capacity=20,
    )
    return BlockGenerationContext(
        term=problem.term,
        selected_block_id=BLOCK_ID,
        selected_block=selected_block,
        selected_classes=problem.classes,
        locked_assignments=(make_locked_assignment(),),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=SCHEDULE_ID,
    )


def test_block_generation_context_is_immutable():
    context = make_block_generation_context()

    with pytest.raises(FrozenInstanceError):
        context.selected_block_id = OTHER_BLOCK_ID


def test_locked_assignment_is_immutable():
    assignment = make_locked_assignment()

    with pytest.raises(FrozenInstanceError):
        assignment.class_id = CLASS_ID


def test_block_generation_context_fixture_is_deterministic():
    first = make_block_generation_context()
    second = make_block_generation_context()

    assert first == second
    assert first.selected_classes == second.selected_classes
    assert first.locked_assignments == second.locked_assignments


def test_block_generation_context_exposes_selected_block_identity():
    context = make_block_generation_context()

    assert context.selected_block_id == BLOCK_ID
    assert context.selected_block.block_id == BLOCK_ID
    assert all(
        cls.block_id == BLOCK_ID for cls in context.selected_classes
    )


def test_block_generation_context_locked_assignments_are_from_other_blocks():
    context = make_block_generation_context()

    for locked in context.locked_assignments:
        assert locked.block_id != BLOCK_ID


def test_block_generation_context_with_no_base_schedule():
    problem = make_scheduling_problem()
    selected_block = BlockSectionDTO(
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="A",
        capacity=20,
    )
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=BLOCK_ID,
        selected_block=selected_block,
        selected_classes=problem.classes,
        locked_assignments=(),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )

    assert context.base_schedule_id is None
    assert context.locked_assignments == ()
