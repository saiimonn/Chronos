from random import Random
from uuid import UUID, uuid4

from app.domain import BlockGenerationContext, BlockSectionDTO, LockedAssignment
from app.services.ga import (
    initialize_population,
    DeterministicRepairOperator,
    GAConfig,
    GAEngine,
    GeneSampler,
    HistoricalScoreBreakdown,
    TerminationReason,
)
from app.services.validation import ConstraintEngine
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    LECTURE_ROOM,
    SECTION_UNSTAFFABLE_ID,
    SLOT_MORNING_ID,
    make_combined_offering_problem,
    make_congested_ga_problem,
    make_ga_problem,
)


def _config(**overrides) -> GAConfig:
    parameters = {
        "population_size": 12,
        "max_generations": 6,
        "tournament_size": 3,
        "elite_count": 1,
        "gene_mutation_rate": 0.3,
        "stagnation_limit": None,
        "random_seed": 20260829,
    } | overrides
    return GAConfig(**parameters)


def _run(config: GAConfig, *, include_unstaffable: bool = True):
    problem = make_ga_problem(include_unstaffable=include_unstaffable)
    return problem, GAEngine(problem, config).run()


def test_same_seed_reproduces_the_run_and_a_different_seed_does_not():
    config = _config()

    _, first = _run(config)
    _, repeat = _run(config)
    _, other = _run(_config(random_seed=config.random_seed + 1))

    assert first.candidate.assignments == repeat.candidate.assignments
    assert first.metrics.generations == repeat.metrics.generations
    assert first.metrics.random_seed == config.random_seed
    assert (
        first.metrics.generations != other.metrics.generations
        or first.candidate.assignments != other.candidate.assignments
    )


def test_final_candidate_only_uses_values_the_sampler_allows():
    config = _config()
    problem, result = _run(config)
    sampler = GeneSampler(problem, config)

    assigned_class_ids = {
        assignment.class_id for assignment in result.candidate.assignments
    }
    assert len(result.candidate.assignments) == len(assigned_class_ids)
    for assignment in result.candidate.assignments:
        assert assignment.class_id != SECTION_UNSTAFFABLE_ID
        offering_id = sampler.offering_for(assignment.class_id)
        assert assignment.faculty_id in sampler.faculty_pool(offering_id)
        assert assignment.time_slot_id in sampler.time_slot_pool(
            offering_id, assignment.faculty_id
        )
        assert assignment.room_code in sampler.room_pool(offering_id)
        assert assignment.meeting_days in config.meeting_day_patterns


def test_generation_limit_terminates_the_run():
    config = _config(max_generations=3)

    _, result = _run(config)

    assert result.metrics.termination_reason is TerminationReason.MAX_GENERATIONS
    assert result.metrics.termination_generation == 3
    assert len(result.metrics.generations) == 4
    assert result.metrics.generations[-1].generation == 3


def test_complete_conflict_free_schedule_keeps_evolving_to_the_limit():
    config = _config(max_generations=20)

    problem, result = _run(config, include_unstaffable=False)

    assert result.metrics.termination_reason is TerminationReason.MAX_GENERATIONS
    assert result.metrics.termination_generation == config.max_generations
    assert result.metrics.first_feasible_generation is not None
    assert result.metrics.first_feasible_generation < config.max_generations
    assert result.metrics.assigned_class_count == len(problem.classes)
    assert result.metrics.assignment_completeness == 1.0
    assert result.metrics.hard_conflict_count == 0


def test_infeasible_run_reports_no_first_feasible_generation():
    _, result = _run(_config(max_generations=3))

    assert result.metrics.first_feasible_generation is None


class _FlexibleTThScorer:
    """Rewards one placement pattern so attainment can rise once feasible."""

    def score(self, problem, assignments):
        share = (
            sum(
                a.meeting_days == "TTh" and a.faculty_id == FACULTY_FLEXIBLE_ID
                for a in assignments
            )
            / len(assignments)
            if assignments
            else 0.0
        )
        return HistoricalScoreBreakdown(
            teacher_subject_score=None,
            teacher_day_score=None,
            teacher_time_score=share,
            teacher_subject_room_score=None,
            historical_pattern_attainment=share,
        )


def test_historical_attainment_keeps_improving_after_feasibility():
    # A two-member population is feasible at generation 0 with zero
    # attainment; the pre-CHR-GA-006 engine stopped there.
    config = _config(population_size=2, tournament_size=2, max_generations=30)
    problem = make_ga_problem(include_unstaffable=False)

    result = GAEngine(problem, config, historical_scorer=_FlexibleTThScorer()).run()

    first = result.metrics.first_feasible_generation
    assert first is not None
    at_feasibility = result.metrics.generations[first].best_fitness
    final = result.metrics.generations[-1].best_fitness
    assert final[:2] == at_feasibility[:2]
    assert final[2] > at_feasibility[2]


def test_without_elites_the_best_schedule_ever_seen_is_returned():
    # With elite_count=0 a later population can lose its best schedule; the
    # run must still return and report the best one it found.
    problem = make_ga_problem(include_unstaffable=False)
    for seed in range(40):
        config = _config(
            population_size=4,
            tournament_size=2,
            elite_count=0,
            max_generations=30,
            random_seed=seed,
        )
        result = GAEngine(problem, config, historical_scorer=_FlexibleTThScorer()).run()

        best = [metric.best_fitness for metric in result.metrics.generations]
        assert best == sorted(best)
        assert result.metrics.historical_pattern_attainment == best[-1][2]
        assert result.metrics.hard_conflict_count == -best[-1][1]


def test_stagnation_window_terminates_the_run():
    config = _config(
        max_generations=50,
        crossover_rate=0.0,
        mutation_rate=0.0,
        stagnation_limit=2,
    )

    _, result = _run(config)

    assert result.metrics.termination_reason is TerminationReason.STAGNATION
    assert result.metrics.termination_generation == 2


def test_elitism_keeps_the_best_fitness_monotonic_across_generations():
    config = _config(population_size=24, max_generations=25, stagnation_limit=None)

    result = GAEngine(make_congested_ga_problem(), config).run()

    best = [metric.best_fitness for metric in result.metrics.generations]
    assert best == sorted(best)


def test_search_improves_on_the_initial_population_under_congestion():
    config = _config(population_size=24, max_generations=25, stagnation_limit=None)

    result = GAEngine(make_congested_ga_problem(), config).run()

    generations = result.metrics.generations
    assert generations[-1].best_fitness > generations[0].best_fitness


def test_reported_metrics_match_the_returned_candidate():
    config = _config()
    problem, result = _run(config)
    report = ConstraintEngine().evaluate(problem, result.candidate.assignments)

    assert result.metrics.required_class_count == len(problem.classes)
    assert result.metrics.assigned_class_count == len(result.candidate.assignments)
    assert result.metrics.hard_conflict_count == report.hard_conflict_count
    assert result.metrics.assignment_completeness == (
        len(result.candidate.assignments) / len(problem.classes)
    )
    assert result.metrics.historical_pattern_attainment == 0.0
    assert result.metrics.generations[-1].best_fitness == (
        result.metrics.assigned_offering_count,
        -result.metrics.hard_conflict_count,
        result.metrics.historical_pattern_attainment,
    )
    assert result.metrics.elapsed_seconds >= 0.0
    assert result.metrics.config == config


def test_ga_engine_evaluates_with_locked_assignments_and_reports_metric_breakdown():
    problem = make_ga_problem(include_unstaffable=False)
    config = _config(max_generations=2, population_size=4)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=problem.faculty[0].faculty_id,
        time_slot_id=problem.time_slots[0].time_slot_id,
        room_code=problem.rooms[0].room_code,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    result = GAEngine(
        problem,
        config,
        locked_assignments=(locked,),
    ).run()

    metrics = result.metrics
    assert metrics.selected_hard_conflict_count is not None
    assert metrics.combined_hard_conflict_count is not None
    assert metrics.cross_block_conflict_count is not None
    assert metrics.combined_hard_conflict_count == metrics.hard_conflict_count
    assert (
        metrics.combined_hard_conflict_count
        == metrics.selected_hard_conflict_count + metrics.cross_block_conflict_count
    )


def test_ga_engine_from_block_context_only_schedules_selected_classes():
    problem = make_ga_problem(include_unstaffable=False)
    selected_classes = (problem.classes[0],)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=problem.faculty[0].faculty_id,
        time_slot_id=problem.time_slots[0].time_slot_id,
        room_code=problem.rooms[0].room_code,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    context = BlockGenerationContext(
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
    config = _config(max_generations=2, population_size=4)

    result = GAEngine.from_block_context(context, config).run()

    assert len(result.candidate.assignments) == len(selected_classes)
    assert result.candidate.assignments[0].class_id == selected_classes[0].class_id
    assert result.metrics.required_class_count == 1
    assert result.metrics.assigned_class_count == 1
    assert result.metrics.selected_hard_conflict_count is not None
    assert result.metrics.combined_hard_conflict_count is not None
    assert result.metrics.cross_block_conflict_count is not None


def test_ga_engine_from_block_context_combined_offering_with_locked_block_has_zero_conflicts():
    combined_problem = make_combined_offering_problem()
    class_a = combined_problem.classes[0]
    class_b = combined_problem.classes[1]
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
    config = _config(max_generations=2, population_size=4)

    result = GAEngine.from_block_context(context, config).run()

    assert result.metrics.selected_hard_conflict_count == 0
    assert result.metrics.cross_block_conflict_count == 0
    assert result.metrics.combined_hard_conflict_count == 0
    assert result.metrics.hard_conflict_count == 0
    assert result.metrics.assignment_completeness == 1.0




class _RecordingRepair:
    """Delegates to the real operator and keeps every result it returned."""

    def __init__(self):
        self.results = []
        self._operator = DeterministicRepairOperator()

    def repair(self, candidate, context):
        result = self._operator.repair(candidate, context)
        self.results.append(result)
        return result


def test_every_varied_offspring_is_repaired_to_a_conflict_free_candidate():
    repair = _RecordingRepair()
    config = _config(population_size=24, max_generations=10)

    GAEngine(make_congested_ga_problem(), config, repair_operator=repair).run()

    assert repair.results
    assert all(result.report.hard_conflict_count == 0 for result in repair.results)


def test_copies_of_repaired_parents_skip_repair_but_copies_of_initial_ones_do_not():
    # Mutation runs on every child but changes no gene, and there is no
    # crossover, so every child is a copy of its parent.
    def calls(max_generations):
        repair = _RecordingRepair()
        config = _config(
            crossover_rate=0.0,
            mutation_rate=1.0,
            gene_mutation_rate=0.0,
            max_generations=max_generations,
        )
        GAEngine(make_congested_ga_problem(), config, repair_operator=repair).run()
        return len(repair.results)

    # Generation 1 copies never-repaired initial individuals, so each child is
    # repaired once; later generations only copy repaired children.
    first = calls(1)
    assert first == _config().population_size - _config().elite_count
    assert calls(4) == first


def _identical_population(engine, config, *, repaired):
    individual = initialize_population(engine._sampler, config, Random(1))[0]
    if repaired:
        individual.repaired = True
    population = [individual] * config.population_size
    engine._evaluate_population(population)
    return population


def test_crossover_reproducing_repaired_parents_skips_repair():
    repair = _RecordingRepair()
    config = _config(crossover_rate=1.0, mutation_rate=0.0, elite_count=0)
    engine = GAEngine(make_congested_ga_problem(), config, repair_operator=repair)

    engine._next_generation(_identical_population(engine, config, repaired=True), Random(2))

    assert repair.results == []


def test_crossover_reproducing_initial_parents_still_repairs():
    repair = _RecordingRepair()
    config = _config(crossover_rate=1.0, mutation_rate=0.0, elite_count=0)
    engine = GAEngine(make_congested_ga_problem(), config, repair_operator=repair)

    children = engine._next_generation(
        _identical_population(engine, config, repaired=False), Random(2)
    )

    assert len(repair.results) == config.population_size
    assert all(child.repaired for child in children)


def test_repair_respects_locked_assignments_from_other_blocks():
    locked = LockedAssignment(
        class_id=uuid4(),
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
        block_id=uuid4(),
    )
    repair = _RecordingRepair()
    config = _config(population_size=24, max_generations=10)

    GAEngine(
        make_congested_ga_problem(),
        config,
        locked_assignments=(locked,),
        repair_operator=repair,
    ).run()

    assert repair.results
    assert all(result.report.hard_conflict_count == 0 for result in repair.results)
    clashes = [
        assignment
        for result in repair.results
        for assignment in result.candidate.assignments
        if assignment.time_slot_id == locked.time_slot_id
        and assignment.meeting_days == locked.meeting_days
        and (
            assignment.faculty_id == locked.faculty_id
            or assignment.room_code == locked.room_code
        )
    ]
    assert clashes == []
