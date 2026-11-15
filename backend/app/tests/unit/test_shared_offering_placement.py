"""CHR-163 / CHR-FIX-003.

A per-Block run sees only the selected Block's classes. When its Offering is
shared with another Block that already holds a placement, the run must reuse
that placement, whether or not the two meetings would overlap.
"""

from random import Random

from app.domain import (
    Assignment,
    BlockGenerationContext,
    BlockSectionDTO,
    CandidateSchedule,
    ConflictCode,
    LockedAssignment,
)
from app.services.ga.config import GAConfig
from app.services.ga.engine import GAEngine
from app.services.ga.operators import GeneSampler
from app.services.ga.repair import DeterministicRepairOperator, RepairContext
from app.services.validation import ConstraintEngine
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    LECTURE_ROOM,
    OFFERING_LECTURE_ID,
    SLOT_AFTERNOON_ID,
    SLOT_MORNING_ID,
    SMALL_ROOM,
    make_combined_offering_problem,
)

COMBINED = make_combined_offering_problem()
CLASS_A, CLASS_B = COMBINED.classes


def _lock(**overrides) -> LockedAssignment:
    values = dict(
        class_id=CLASS_B.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="TTh",
        block_id=CLASS_B.block_id,
        offering_id=OFFERING_LECTURE_ID,
        units=3,
    )
    values.update(overrides)
    return LockedAssignment(**values)


def _context(*locks: LockedAssignment) -> BlockGenerationContext:
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
        locked_assignments=locks,
        faculty=COMBINED.faculty,
        subjects=COMBINED.subjects,
        rooms=COMBINED.rooms,
        programs=COMBINED.programs,
        time_slots=COMBINED.time_slots,
        offering_block_ids=COMBINED.offering_block_ids,
    )


def _assignment(**overrides) -> Assignment:
    values = dict(
        class_id=CLASS_A.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="TTh",
    )
    values.update(overrides)
    return Assignment(**values)


def _codes(context: BlockGenerationContext, assignment: Assignment) -> set:
    report = ConstraintEngine().evaluate(
        context.selected_problem,
        (assignment,),
        locked_assignments=context.locked_assignments,
        offering_block_ids=context.offering_block_ids,
    )
    return {conflict.code for conflict in report.conflicts}


def test_disjoint_day_placement_of_a_shared_offering_is_a_mismatch():
    # The lock meets TTh and the candidate MWF: no overlap, still two sessions.
    context = _context(_lock())

    assert ConflictCode.SHARED_OFFERING_MISMATCH in _codes(
        context, _assignment(meeting_days="MWF")
    )


def test_different_slot_or_room_of_a_shared_offering_is_a_mismatch():
    context = _context(_lock())

    for assignment in (
        _assignment(time_slot_id=SLOT_AFTERNOON_ID),
        _assignment(room_code=SMALL_ROOM),
    ):
        assert ConflictCode.SHARED_OFFERING_MISMATCH in _codes(context, assignment)


def test_identical_placement_of_a_shared_offering_is_clean():
    assert _codes(_context(_lock()), _assignment()) == set()


def test_sampling_only_draws_the_locked_placement():
    sampler = GeneSampler(_context(_lock()).selected_problem, GAConfig())
    rng = Random(7)

    genes = {sampler.sample_gene(OFFERING_LECTURE_ID, rng) for _ in range(20)}

    assert {
        (gene.faculty_id, gene.time_slot_id, gene.room_code, gene.meeting_days)
        for gene in genes
    } == {(FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "TTh")}


def test_repair_moves_a_mismatched_block_onto_the_locked_placement():
    result = DeterministicRepairOperator().repair(
        CandidateSchedule(
            term_id=COMBINED.term.term_id,
            assignments=(
                _assignment(meeting_days="MWF", time_slot_id=SLOT_AFTERNOON_ID),
            ),
        ),
        RepairContext.from_block_context(_context(_lock())),
    )

    assert result.candidate.assignments == (_assignment(),)
    assert result.report.conflicts == ()


def test_an_illegal_locked_placement_leaves_the_offering_unresolved():
    # The locked room is too small for the Block, so no legal placement remains.
    context = _context(_lock(room_code=SMALL_ROOM))
    sampler = GeneSampler(context.selected_problem, GAConfig())

    gene = sampler.sample_gene(OFFERING_LECTURE_ID, Random(0))
    result = DeterministicRepairOperator().repair(
        CandidateSchedule(
            term_id=COMBINED.term.term_id, assignments=(_assignment(),)
        ),
        RepairContext.from_block_context(context),
    )

    assert gene.faculty_id is None
    assert result.candidate.assignments == ()
    assert result.unresolved_section_ids == (CLASS_A.class_id,)


def test_per_block_ga_returns_the_locked_placement():
    context = _context(_lock())

    result = GAEngine.from_block_context(
        context,
        GAConfig(population_size=6, max_generations=3, random_seed=3),
    ).run()

    assert result.candidate.assignments == (_assignment(),)
    assert _codes(context, result.candidate.assignments[0]) == set()
