from dataclasses import replace
from uuid import UUID

import pytest

from app.domain import (
    Assignment,
    BlockGenerationContext,
    BlockSectionDTO,
    CandidateSchedule,
    LockedAssignment,
)
from app.services.ga import (
    DeterministicRepairOperator,
    GAConfig,
    RepairContext,
)
from app.domain.validation import ConflictCode
from app.services.validation import ConstraintEngine, ConstraintEvaluationError
from app.services.validation.availability import AvailabilityMaskCodec
from app.tests.support.ga import (
    COMBINED_CLASS_A_ID,
    COMBINED_CLASS_B_ID,
    FACULTY_FLEXIBLE_ID,
    FACULTY_MORNING_ID,
    GA_TERM_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    OFFERING_LECTURE_ID,
    OFFERING_LAB_ID,
    OFFERING_UNSTAFFABLE_ID,
    SECTION_LECTURE_ID,
    SECTION_LAB_ID,
    SECTION_UNSTAFFABLE_ID,
    SLOT_AFTERNOON_ID,
    SLOT_LAB_ID,
    SLOT_MORNING_ID,
    SMALL_ROOM,
    make_combined_offering_problem,
    make_congested_ga_problem,
    make_ga_problem,
)


def _context(problem=None, **overrides) -> RepairContext:
    config = GAConfig(**({"random_seed": 1234} | overrides))
    return RepairContext.build(problem or make_ga_problem(), config)


def _candidate(*assignments: Assignment) -> CandidateSchedule:
    return CandidateSchedule(term_id=GA_TERM_ID, assignments=assignments)


def _assignment(class_id: UUID, **overrides) -> Assignment:
    parameters = {
        "class_id": class_id,
        "faculty_id": FACULTY_FLEXIBLE_ID,
        "time_slot_id": SLOT_MORNING_ID,
        "room_code": LABORATORY_ROOM,
        "meeting_days": "MW",
    } | overrides
    return Assignment(**parameters)


def test_valid_candidate_is_returned_unchanged():
    context = _context()
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID, room_code=LECTURE_ROOM),
        _assignment(SECTION_LAB_ID, time_slot_id=SLOT_LAB_ID),
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.changed is False
    assert result.candidate.assignments == candidate.assignments
    assert result.report.conflicts == ()


def test_conflicting_sections_are_repaired_to_a_conflict_free_candidate():
    context = _context()
    candidate = _candidate(
        # Both sections hold the same faculty, day pattern, and slot, each in
        # the only room type it may take.
        _assignment(SECTION_LECTURE_ID, room_code=LECTURE_ROOM),
        _assignment(SECTION_LAB_ID),
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.report.conflicts == ()
    assert result.unresolved_section_ids == ()
    assert result.repaired_section_ids == (SECTION_LECTURE_ID, SECTION_LAB_ID)
    assert result.candidate.assignments == (
        # LABs are repaired first: the LAB takes the only compatible
        # 150-minute slot, after which the lecture's morning slot no longer
        # clashes and is kept.
        _assignment(
            SECTION_LECTURE_ID, time_slot_id=SLOT_MORNING_ID, room_code=LECTURE_ROOM
        ),
        _assignment(SECTION_LAB_ID, time_slot_id=SLOT_LAB_ID),
    )


def test_repair_visits_faculty_rooms_patterns_and_slots_in_stable_order():
    problem = make_ga_problem()
    # Only the morning faculty is included, and only the lecture room is
    # large enough, so the search has one legal placement left.
    problem = replace(
        problem,
        faculty=tuple(
            item
            for item in problem.faculty
            if item.faculty_id != FACULTY_FLEXIBLE_ID
        ),
        rooms=tuple(room for room in problem.rooms if room.room_code == LECTURE_ROOM),
    )
    context = _context(problem)
    candidate = _candidate(
        _assignment(
            SECTION_LECTURE_ID,
            faculty_id=FACULTY_MORNING_ID,
            time_slot_id=SLOT_AFTERNOON_ID,
            room_code=LECTURE_ROOM,
        )
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.candidate.assignments == (
        _assignment(
            SECTION_LECTURE_ID,
            faculty_id=FACULTY_MORNING_ID,
            time_slot_id=SLOT_MORNING_ID,
            room_code=LECTURE_ROOM,
        ),
    )


def test_section_without_any_legal_placement_is_left_unresolved():
    context = _context()
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID, room_code=LECTURE_ROOM),
        # No room is large enough for this section.
        _assignment(SECTION_UNSTAFFABLE_ID, time_slot_id=SLOT_AFTERNOON_ID),
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.unresolved_section_ids == (SECTION_UNSTAFFABLE_ID,)
    assert result.repaired_section_ids == ()
    assert result.candidate.assignments == (
        _assignment(SECTION_LECTURE_ID, room_code=LECTURE_ROOM),
    )
    assert result.report.conflicts == ()


def test_unassigned_sections_are_never_given_an_assignment():
    context = _context()
    candidate = _candidate(_assignment(SECTION_LECTURE_ID, room_code=LECTURE_ROOM))

    result = DeterministicRepairOperator().repair(candidate, context)

    assigned = {
        assignment.class_id for assignment in result.candidate.assignments
    }
    assert assigned == {SECTION_LECTURE_ID}


@pytest.mark.parametrize("missing_field", ["room_code", "meeting_days"])
def test_incomplete_assignments_are_treated_as_infeasible(missing_field):
    context = _context()
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID, **{missing_field: None}),
        _assignment(SECTION_LAB_ID, time_slot_id=SLOT_LAB_ID),
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.repaired_section_ids == (SECTION_LECTURE_ID,)
    repaired = result.candidate.assignments[0]
    assert repaired.room_code is not None
    assert repaired.meeting_days is not None
    assert result.report.conflicts == ()


def test_equivalent_inputs_produce_identical_repair_output():
    problem = make_congested_ga_problem()
    context = _context(problem)
    class_ids = tuple(section.class_id for section in problem.classes)
    assignments = tuple(
        _assignment(class_id, room_code=LECTURE_ROOM) for class_id in class_ids
    )

    first = DeterministicRepairOperator().repair(_candidate(*assignments), context)
    second = DeterministicRepairOperator().repair(_candidate(*assignments), context)
    # The same assignments in a different input order are the same schedule.
    shuffled = DeterministicRepairOperator().repair(
        _candidate(*reversed(assignments)),
        RepairContext.build(problem, context.config),
    )

    assert first == second
    assert first == shuffled


def test_repair_of_a_congested_candidate_removes_every_hard_conflict():
    problem = make_congested_ga_problem()
    context = _context(problem)
    candidate = _candidate(
        *(
            _assignment(section.class_id, room_code=LECTURE_ROOM)
            for section in problem.classes
        )
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.report.conflicts == ()
    assert len(result.candidate.assignments) + len(
        result.unresolved_section_ids
    ) == len(problem.classes)


def test_repairing_a_repaired_candidate_changes_nothing():
    context = _context()
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID),
        _assignment(SECTION_LAB_ID),
    )
    operator = DeterministicRepairOperator()

    first = operator.repair(candidate, context)
    second = operator.repair(first.candidate, context)

    assert second.changed is False
    assert second.candidate.assignments == first.candidate.assignments


def test_schedule_identity_is_preserved():
    context = _context()
    schedule_id = UUID("00000000-0000-0000-0000-0000000000c1")
    candidate = CandidateSchedule(
        term_id=GA_TERM_ID,
        assignments=(_assignment(SECTION_LECTURE_ID),),
        schedule_id=schedule_id,
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.candidate.schedule_id == schedule_id
    assert result.candidate.term_id == GA_TERM_ID


def test_candidate_from_another_term_is_rejected():
    context = _context()
    candidate = CandidateSchedule(
        term_id=UUID("00000000-0000-0000-0000-0000000000d1"),
        assignments=(),
    )

    with pytest.raises(ConstraintEvaluationError):
        DeterministicRepairOperator().repair(candidate, context)


def test_unknown_and_duplicated_sections_are_rejected():
    context = _context()
    unknown = _candidate(
        _assignment(UUID("00000000-0000-0000-0000-0000000000d2")),
    )
    duplicated = _candidate(
        _assignment(SECTION_LECTURE_ID),
        _assignment(SECTION_LECTURE_ID, time_slot_id=SLOT_AFTERNOON_ID),
    )
    operator = DeterministicRepairOperator()

    with pytest.raises(ConstraintEvaluationError):
        operator.repair(unknown, context)
    with pytest.raises(ConstraintEvaluationError):
        operator.repair(duplicated, context)


def test_repairing_a_combined_offering_keeps_one_assignment_per_block():
    problem = make_combined_offering_problem()
    context = _context(problem)
    # SMALL_ROOM seats ten and the combined session needs forty, so both
    # Blocks arrive in conflict and have to move together.
    candidate = _candidate(
        *(
            _assignment(class_id, room_code=SMALL_ROOM)
            for class_id in (COMBINED_CLASS_A_ID, COMBINED_CLASS_B_ID)
        )
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert tuple(item.class_id for item in result.candidate.assignments) == (
        COMBINED_CLASS_A_ID,
        COMBINED_CLASS_B_ID,
    )
    placements = {
        (item.faculty_id, item.time_slot_id, item.room_code, item.meeting_days)
        for item in result.candidate.assignments
    }
    assert len(placements) == 1
    assert result.report.conflicts == ()
    assert result.repaired_section_ids == (
        COMBINED_CLASS_A_ID,
        COMBINED_CLASS_B_ID,
    )


def test_repairing_a_combined_offering_twice_is_a_no_op():
    problem = make_combined_offering_problem()
    context = _context(problem)
    operator = DeterministicRepairOperator()
    candidate = _candidate(
        *(
            _assignment(class_id, room_code=SMALL_ROOM)
            for class_id in (COMBINED_CLASS_A_ID, COMBINED_CLASS_B_ID)
        )
    )

    once = operator.repair(candidate, context)
    twice = operator.repair(once.candidate, context)

    assert twice.candidate.assignments == once.candidate.assignments
    assert twice.changed is False


def test_repair_avoids_locked_assignment():
    problem = make_ga_problem(include_unstaffable=False)
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID, time_slot_id=SLOT_MORNING_ID, room_code=LECTURE_ROOM),
    )
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LABORATORY_ROOM,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    context = RepairContext.build(problem, GAConfig(random_seed=42), locked_assignments=(locked,))

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.changed is True
    assert result.repaired_section_ids == (SECTION_LECTURE_ID,)
    assert result.unresolved_section_ids == ()
    assert result.report.conflicts == ()
    assert len(result.candidate.assignments) == 1
    assert result.candidate.assignments[0].time_slot_id == SLOT_AFTERNOON_ID


def test_repair_leaves_offering_unresolved_when_all_alternatives_collide_with_locks():
    problem = make_ga_problem(include_unstaffable=False)
    candidate = _candidate(
        _assignment(SECTION_LECTURE_ID, time_slot_id=SLOT_MORNING_ID, room_code=LECTURE_ROOM),
        _assignment(SECTION_LAB_ID, time_slot_id=SLOT_LAB_ID, room_code=LABORATORY_ROOM),
    )
    other_block = UUID("00000000-0000-0000-0000-000000000088")
    locked = []
    idx = 1
    for fac in (FACULTY_FLEXIBLE_ID, FACULTY_MORNING_ID):
        for slot in (SLOT_MORNING_ID, SLOT_AFTERNOON_ID):
            for days in ("MWF", "TTh"):
                locked.append(
                    LockedAssignment(
                        class_id=UUID(f"00000000-0000-0000-0000-{idx:012d}"),
                        faculty_id=fac,
                        time_slot_id=slot,
                        room_code="<ROOM-1>",
                        meeting_days=days,
                        block_id=other_block,
                    )
                )
                idx += 1

    context = RepairContext.build(
        problem,
        GAConfig(random_seed=42),
        locked_assignments=tuple(locked),
    )

    result = DeterministicRepairOperator().repair(candidate, context)

    assert result.changed is True
    assert result.repaired_section_ids == ()
    assert result.unresolved_section_ids == (SECTION_LECTURE_ID,)
    assert len(result.candidate.assignments) == 1
    assert result.candidate.assignments[0].class_id == SECTION_LAB_ID
    assert result.report.conflicts == ()


def test_repair_from_block_context():
    problem = make_ga_problem(include_unstaffable=False)
    selected_classes = (problem.classes[0],)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LABORATORY_ROOM,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    block_context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000001"),
        selected_block=BlockSectionDTO(
            block_id=UUID("00000000-0000-0000-0000-000000000001"),
            term_id=problem.term.term_id,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=selected_classes,
        locked_assignments=(locked,),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )

    repair_ctx = RepairContext.from_block_context(block_context)
    assert repair_ctx.locked_assignments == (locked,)
    assert repair_ctx.problem.classes == selected_classes
    assert repair_ctx.offering_block_ids == block_context.offering_block_ids


def test_repair_from_block_context_combined_offering_no_conflicts():
    combined_problem = make_combined_offering_problem()
    class_a = combined_problem.classes[0]
    class_b = combined_problem.classes[1]
    candidate = _candidate(
        Assignment(
            class_id=class_a.class_id,
            faculty_id=FACULTY_FLEXIBLE_ID,
            time_slot_id=SLOT_MORNING_ID,
            room_code=LECTURE_ROOM,
            meeting_days="MWF",
        )
    )
    locked = LockedAssignment(
        class_id=class_b.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
        block_id=class_b.block_id,
    )
    context = BlockGenerationContext(
        term=combined_problem.term,
        selected_block_id=class_a.block_id,
        selected_block=BlockSectionDTO(
            block_id=class_a.block_id,
            term_id=combined_problem.term.term_id,
            program_code=class_a.program_code,
            year_level=class_a.year_level,
            section_letter=class_a.section_letter,
            capacity=class_a.capacity,
        ),
        selected_classes=(class_a,),
        locked_assignments=(locked,),
        faculty=combined_problem.faculty,
        subjects=combined_problem.subjects,
        rooms=combined_problem.rooms,
        programs=combined_problem.programs,
        time_slots=combined_problem.time_slots,
        offering_block_ids=combined_problem.offering_block_ids,
        base_schedule_id=None,
    )
    repair_ctx = RepairContext.from_block_context(context, GAConfig(random_seed=42))

    result = DeterministicRepairOperator().repair(candidate, repair_ctx)

    assert result.changed is False
    assert result.repaired_section_ids == ()
    assert result.unresolved_section_ids == ()
    assert result.report.conflicts == ()
    assert len(result.candidate.assignments) == 1
    assert result.candidate.assignments[0].class_id == class_a.class_id




@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("problem_factory", [make_congested_ga_problem, make_combined_offering_problem])
@pytest.mark.parametrize("with_lock", [None, "legacy", "loaded"])
def test_clash_screen_agrees_with_the_constraint_engine(seed, problem_factory, with_lock):
    """The screen must match a full-working-set engine check on every
    alternative, and repair (screen, then an engine check on the related
    assignments only) must accept the same alternative that check would."""
    from random import Random

    from app.services.ga.chromosome import ChromosomeCodec
    from app.services.ga.operators import initialize_population
    from app.services.ga.repair import _ClashScreen
    from app.services.validation import ConstraintEngine

    problem = problem_factory()
    context = _context(problem, population_size=4, tournament_size=2, random_seed=seed)
    if with_lock:
        context = replace(
            context,
            locked_assignments=(
                LockedAssignment(
                    class_id=UUID(int=seed + 1),
                    faculty_id=FACULTY_FLEXIBLE_ID,
                    time_slot_id=SLOT_MORNING_ID,
                    room_code=LECTURE_ROOM,
                    meeting_days="MWF",
                    block_id=UUID(int=seed + 100),
                    # A loaded lock carries another Offering's teaching load,
                    # which both the screen and the engine must count.
                    **(
                        {"offering_id": UUID(int=seed + 200), "units": 15}
                        if with_lock == "loaded"
                        else {}
                    ),
                ),
            ),
        )
    codec = ChromosomeCodec(problem)
    engine = ConstraintEngine()
    checked = rejected = 0
    for genes in initialize_population(context.sampler, context.config, Random(seed)):
        assignments = {a.class_id: a for a in codec.decode(genes).assignments}
        for offering_id in codec.offering_ids:
            class_ids = codec.class_ids_for(offering_id)
            working = {k: v for k, v in assignments.items() if k not in class_ids}
            screen = _ClashScreen(offering_id, class_ids, working, context)
            assert DeterministicRepairOperator()._first_valid_alternative(
                offering_id, working, context
            ) == next(
                (
                    alternative
                    for alternative in DeterministicRepairOperator._alternatives(offering_id, context)
                    if not screen.rejects(alternative)
                ),
                None,
            )
            for alternative in DeterministicRepairOperator._alternatives(offering_id, context):
                trial = dict(working)
                trial.update(DeterministicRepairOperator._per_block(alternative, class_ids))
                report = engine.evaluate(
                    problem,
                    tuple(trial[c] for c in codec.class_ids if c in trial),
                    locked_assignments=context.locked_assignments,
                    offering_block_ids=context.offering_block_ids,
                )
                engine_rejects = any(
                    class_id in conflict.class_ids
                    for conflict in report.conflicts
                    for class_id in class_ids
                )
                assert screen.rejects(alternative) == engine_rejects
                checked += 1
                rejected += engine_rejects
    assert checked
    if problem_factory is make_congested_ga_problem:
        assert 0 < rejected < checked


class _NoSlotAvailable(AvailabilityMaskCodec):
    """Marks every faculty member unavailable at every slot."""

    def available_slot_ids(self, *_args, **_kwargs):
        return frozenset()


def test_the_injected_constraint_engine_decides_which_alternative_is_accepted():
    # The sampler pools still offer these slots, so the clash screen passes
    # them; only the engine knows they are unavailable.
    operator = DeterministicRepairOperator(ConstraintEngine(_NoSlotAvailable()))
    candidate = _candidate(_assignment(SECTION_LECTURE_ID), _assignment(SECTION_LAB_ID))

    result = operator.repair(candidate, _context())

    assert result.repaired_section_ids == ()
    assert ConflictCode.FACULTY_UNAVAILABLE not in {
        conflict.code for conflict in result.report.conflicts
    }
