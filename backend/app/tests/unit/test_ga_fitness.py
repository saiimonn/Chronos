from dataclasses import replace
from uuid import UUID

import pytest

from app.domain import Assignment, LockedAssignment
from app.services.ga import (
    ChromosomeCodec,
    HistoricalScoreBreakdown,
    LexicographicFitnessEvaluator,
    ScheduleGene,
    create_individual,
    register_deap_types,
)
from app.tests.support.ga import (
    COMBINED_CLASS_A_ID,
    COMBINED_CLASS_B_ID,
    FACULTY_FLEXIBLE_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    OFFERING_LECTURE_ID,
    SLOT_MORNING_ID,
    make_combined_offering_problem,
)
from app.tests.support.scheduling import (
    FACULTY_ID,
    CLASS_ID,
    OFFERING_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
)


CLASS_2_ID = UUID("00000000-0000-0000-0000-000000000006")
OFFERING_2_ID = UUID("00000000-0000-0000-0000-000000000008")


def _problem():
    problem = make_scheduling_problem()
    second_class = replace(
        problem.classes[0], class_id=CLASS_2_ID, offering_id=OFFERING_2_ID
    )
    classes = (second_class, problem.classes[0])
    return replace(
        problem,
        classes=classes,
        offering_block_ids={
            OFFERING_2_ID: (CLASS_2_ID,),
            OFFERING_ID: (CLASS_ID,),
        },
    )


def _assignment(class_id: UUID) -> Assignment:
    return Assignment(
        class_id=class_id,
        faculty_id=FACULTY_ID,
        time_slot_id=TIME_SLOT_ID,
        room_code="<ROOM-1>",
        meeting_days="MWF",
    )


def test_chromosome_has_stable_offering_order_and_explicit_unresolved_gene():
    problem = _problem()
    codec = ChromosomeCodec(problem)

    individual = create_individual(problem, (_assignment(CLASS_2_ID),))
    candidate = codec.decode(individual)

    assert codec.offering_ids == (OFFERING_ID, OFFERING_2_ID)
    assert [gene.offering_id for gene in individual] == [OFFERING_ID, OFFERING_2_ID]
    assert individual[0].is_resolved is False
    assert individual[1].is_resolved is True
    assert candidate.assignments == (_assignment(CLASS_2_ID),)


def test_gene_rejects_partially_populated_assignment():
    with pytest.raises(ValueError, match="fully assigned or unresolved"):
        ScheduleGene(
            offering_id=OFFERING_ID,
            class_ids=(CLASS_ID,),
            faculty_id=FACULTY_ID,
        )

    with pytest.raises(ValueError, match="include room_code and meeting_days"):
        ChromosomeCodec(_problem()).encode(
            (replace(_assignment(CLASS_ID), room_code=None),)
        )


def test_codec_rejects_reordered_or_duplicate_offering_genes():
    problem = _problem()
    codec = ChromosomeCodec(problem)
    genes = codec.encode()

    with pytest.raises(ValueError, match="stable offering order"):
        codec.decode(tuple(reversed(genes)))
    with pytest.raises(ValueError, match="duplicate assignment"):
        codec.encode((_assignment(CLASS_ID), _assignment(CLASS_ID)))


def _combined_gene(**placement) -> ScheduleGene:
    codec = ChromosomeCodec(make_combined_offering_problem())
    return replace(codec.encode()[0], **placement)


def test_one_gene_carries_every_block_of_a_combined_offering():
    codec = ChromosomeCodec(make_combined_offering_problem())

    genes = codec.encode()

    assert codec.offering_ids == (OFFERING_LECTURE_ID,)
    assert genes[0].class_ids == (COMBINED_CLASS_A_ID, COMBINED_CLASS_B_ID)
    assert codec.class_ids_for(OFFERING_LECTURE_ID) == genes[0].class_ids
    assert codec.offering_for(COMBINED_CLASS_B_ID) == OFFERING_LECTURE_ID


def test_a_combined_gene_decodes_to_one_assignment_per_block_and_round_trips():
    codec = ChromosomeCodec(make_combined_offering_problem())
    placed = _combined_gene(
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )

    candidate = codec.decode((placed,))

    assert tuple(item.class_id for item in candidate.assignments) == (
        COMBINED_CLASS_A_ID,
        COMBINED_CLASS_B_ID,
    )
    assert {
        (item.faculty_id, item.time_slot_id, item.room_code, item.meeting_days)
        for item in candidate.assignments
    } == {(FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "MWF")}
    # The Blocks of one offering describe a single session, so they collapse
    # back into the gene they came from.
    assert codec.encode(candidate.assignments) == (placed,)


def test_codec_rejects_blocks_of_one_offering_placed_differently():
    codec = ChromosomeCodec(make_combined_offering_problem())
    placed = _combined_gene(
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    first, second = codec.decode((placed,)).assignments

    with pytest.raises(ValueError, match="conflicting assignments for offering"):
        codec.encode((first, replace(second, room_code=LABORATORY_ROOM)))


def test_codec_orders_genes_by_the_stable_section_key():
    problem = make_combined_offering_problem()
    reversed_problem = replace(problem, classes=tuple(reversed(problem.classes)))

    assert (
        ChromosomeCodec(reversed_problem).class_ids
        == ChromosomeCodec(problem).class_ids
    )


class _FixedHistoricalScorer:
    def __init__(self, score: float):
        self.score_value = score
        self.received: tuple[Assignment, ...] | None = None

    def score(self, problem, assignments) -> HistoricalScoreBreakdown:
        self.received = tuple(assignments)
        return HistoricalScoreBreakdown(
            teacher_subject_score=self.score_value,
            teacher_day_score=None,
            teacher_time_score=None,
            teacher_subject_room_score=None,
            historical_pattern_attainment=self.score_value,
        )


def test_evaluator_uses_shared_constraints_and_does_not_mutate_candidate():
    problem = _problem()
    assignments = (_assignment(CLASS_2_ID), _assignment(CLASS_ID))
    individual = create_individual(problem, assignments)
    original = tuple(individual)
    scorer = _FixedHistoricalScorer(0.75)

    values = LexicographicFitnessEvaluator(problem, scorer)(individual)

    assert values == (2, -3, 0.75)
    assert scorer.received == (_assignment(CLASS_ID), _assignment(CLASS_2_ID))
    assert tuple(individual) == original


def _fitness(values):
    fitness_type, _ = register_deap_types()
    fitness = fitness_type()
    fitness.values = values
    return fitness


def test_deap_fitness_enforces_thesis_lexicographic_ranking():
    complete = _fitness((100, -5, 0.1))
    incomplete = _fitness((99, 0, 1.0))
    fewer_conflicts = _fitness((100, -1, 0.1))
    better_history = _fitness((100, 0, 0.8))

    assert complete > incomplete
    assert complete.dominates(incomplete)
    assert complete.dominates(incomplete, [0, 2])
    assert not complete.dominates(incomplete, [1, 2])
    assert fewer_conflicts > _fitness((100, -2, 1.0))
    assert fewer_conflicts.dominates(_fitness((100, -2, 1.0)))
    assert better_history > _fitness((100, 0, 0.2))
    assert better_history.dominates(_fitness((100, 0, 0.2)))


def test_deap_registration_is_idempotent():
    first = register_deap_types()
    second = register_deap_types()

    assert first == second


def test_fitness_evaluator_penalizes_cross_block_conflicts_in_second_tuple_element():
    problem = _problem()
    individual = create_individual(problem, (_assignment(CLASS_ID),))
    evaluator_no_locks = LexicographicFitnessEvaluator(problem)
    values_no_locks = evaluator_no_locks(individual)
    assert values_no_locks == (1, 0, 0.0)

    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_ID,
        time_slot_id=TIME_SLOT_ID,
        room_code="<ROOM-2>",
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    evaluator_with_locks = LexicographicFitnessEvaluator(
        problem,
        locked_assignments=(locked,),
    )
    values_with_locks = evaluator_with_locks(individual)
    assert values_with_locks == (1, -1, 0.0)


def test_lexicographic_ranking_historical_score_breaks_only_exact_ties():
    fit_a = _fitness((1, 0, 0.9))
    fit_b = _fitness((1, 0, 0.1))
    assert fit_a > fit_b
    assert fit_a.dominates(fit_b)

    fit_c = _fitness((1, 0, 0.1))
    fit_d = _fitness((1, -1, 1.0))
    assert fit_c > fit_d
    assert fit_c.dominates(fit_d)

