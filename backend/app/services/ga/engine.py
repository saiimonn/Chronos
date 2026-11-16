"""CHR-29 / CHR-GA-002 bounded genetic-algorithm loop and termination."""

from collections.abc import Mapping, Sequence
from operator import attrgetter
from random import Random
from time import perf_counter
from uuid import UUID

from app.domain import (
    BlockGenerationContext,
    CandidateSchedule,
    LockedAssignment,
    SchedulingProblem,
)
from app.services.ga.chromosome import (
    ChromosomeCodec,
    ScheduleGene,
    register_deap_types,
)
from app.services.ga.config import GAConfig
from app.services.ga.fitness import (
    FitnessValues,
    HistoricalAttainmentScorer,
    LexicographicFitnessEvaluator,
)
from app.services.ga.metrics import (
    GARunMetrics,
    GARunResult,
    GenerationMetric,
    TerminationReason,
)
from app.services.ga.operators import (
    GeneSampler,
    crossover,
    initialize_population,
    mutate,
)
from app.services.ga.repair import (
    DeterministicRepairOperator,
    RepairContext,
    RepairOperator,
)
from app.services.validation import ConstraintEngine


class GAEngine:
    """Runs the bounded operator loop and reports why the run stopped.

    The engine owns a single seeded ``Random``; every operator draws from it,
    so a seed plus a config reproduces a run exactly. Offspring are only ever
    recombined at gene boundaries or resampled from the sampler pools, so no
    stage of the loop can produce a structurally invalid candidate.

    Every offspring that crossover or mutation changed passes through
    deterministic repair (CHR-GA-003) before evaluation, as the thesis
    procedure requires (CHR-GA-005). Repair draws no random numbers, so it
    leaves seeded reproducibility intact. Offspring copied unchanged from a
    repaired parent skip it.

    With ``repair_initial_population`` (master-schedule generation) the
    initial population is repaired too. Fitness ranks "more offerings placed"
    above "fewer hard conflicts", so an unrepaired random individual that
    places everything with a double-booking outranks every repaired,
    conflict-free one that had to leave an offering out; elitism then carries
    it to the end of the run and the proposal comes back double-booked. The
    thesis runs leave it off so their convergence metrics keep their meaning.
    """

    def __init__(
        self,
        problem: SchedulingProblem,
        config: GAConfig,
        historical_scorer: HistoricalAttainmentScorer | None = None,
        constraint_engine: ConstraintEngine | None = None,
        locked_assignments: Sequence[LockedAssignment] | None = None,
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
        repair_operator: RepairOperator | None = None,
        day_balance_weight: float = 0.0,
        repair_initial_population: bool = False,
    ):
        self._problem = problem
        self._day_balance_weight = day_balance_weight
        self._repair_initial_population = repair_initial_population
        self._config = config
        self._locked_assignments = tuple(locked_assignments or ())
        self._offering_block_ids = offering_block_ids
        self._constraint_engine = constraint_engine or ConstraintEngine()
        self._codec = ChromosomeCodec(problem)
        self._sampler = GeneSampler(
            problem, config, locked_assignments=self._locked_assignments
        )
        self._evaluate = LexicographicFitnessEvaluator(
            problem,
            historical_scorer,
            self._constraint_engine,
            locked_assignments=self._locked_assignments,
            offering_block_ids=self._offering_block_ids,
            day_balance_weight=day_balance_weight,
        )
        self._repair_operator = repair_operator or DeterministicRepairOperator(
            self._constraint_engine
        )
        self._repair_context = RepairContext(
            problem=problem,
            config=config,
            sampler=self._sampler,
            locked_assignments=self._locked_assignments,
            offering_block_ids=self._offering_block_ids,
            balance_days=day_balance_weight > 0,
        )
        _, self._individual_type = register_deap_types()

    @classmethod
    def from_block_context(
        cls,
        context: BlockGenerationContext,
        config: GAConfig,
        historical_scorer: HistoricalAttainmentScorer | None = None,
        constraint_engine: ConstraintEngine | None = None,
        repair_operator: RepairOperator | None = None,
        day_balance_weight: float = 0.0,
        repair_initial_population: bool = False,
    ) -> "GAEngine":
        return cls(
            problem=context.selected_problem,
            config=config,
            historical_scorer=historical_scorer,
            constraint_engine=constraint_engine,
            locked_assignments=context.locked_assignments,
            offering_block_ids=context.offering_block_ids,
            repair_operator=repair_operator,
            day_balance_weight=day_balance_weight,
            repair_initial_population=repair_initial_population,
        )

    def run(self) -> GARunResult:
        started_at = perf_counter()
        rng = Random(self._config.random_seed)

        population = initialize_population(self._sampler, self._config, rng)
        if self._repair_initial_population:
            population = [self._repaired_individual(item) for item in population]
        self._evaluate_population(population)
        # CHR-GA-006: a feasible run keeps evolving, and with elite_count=0 a
        # later population can lose its best schedule, so the best ever seen
        # is kept and returned. Ties go to the newer individual, which with
        # elitism is exactly the final population's best.
        best = self._best(population)
        generations = [self._record(0, population, best)]
        generation = 0
        stagnant_generations = 0
        first_feasible_generation = (
            0 if self._is_feasible(generations[-1].best_fitness) else None
        )

        while True:
            reason = self._termination_reason(
                generation,
                generations[-1].best_fitness,
                stagnant_generations,
            )
            if reason is not None:
                break

            # Compared on the full fitness, so a day-balance gain counts as
            # progress even though the recorded metric leaves it out.
            previous_best = best.fitness.values
            population = self._next_generation(population, rng)
            self._evaluate_population(population)
            generation += 1
            challenger = self._best(population)
            if challenger.fitness >= best.fitness:
                best = challenger
            generations.append(self._record(generation, population, best))
            stagnant_generations = (
                0
                if best.fitness.values > previous_best
                else stagnant_generations + 1
            )
            if first_feasible_generation is None and self._is_feasible(
                generations[-1].best_fitness
            ):
                first_feasible_generation = generation

        candidate = self._codec.decode(best)
        best_fitness: FitnessValues = best.fitness.values
        assigned_offering_count = int(best_fitness[0])
        assigned_class_count = len(candidate.assignments)
        combined_hard_conflict_count = -int(best_fitness[1])

        selected_report = self._constraint_engine.evaluate(
            self._problem,
            candidate.assignments,
            locked_assignments=None,
            offering_block_ids=self._offering_block_ids,
        )
        selected_hard_conflict_count = selected_report.hard_conflict_count
        cross_block_conflict_count = max(
            0, combined_hard_conflict_count - selected_hard_conflict_count
        )

        return GARunResult(
            candidate=candidate,
            metrics=GARunMetrics(
                random_seed=self._config.random_seed,
                config=self._config,
                termination_reason=reason,
                termination_generation=generation,
                first_feasible_generation=first_feasible_generation,
                elapsed_seconds=perf_counter() - started_at,
                generations=tuple(generations),
                required_offering_count=len(self._codec.offering_ids),
                assigned_offering_count=assigned_offering_count,
                required_class_count=len(self._codec.class_ids),
                assigned_class_count=assigned_class_count,
                hard_conflict_count=combined_hard_conflict_count,
                historical_pattern_attainment=(
                    self._evaluate.historical_attainment(best)
                    if self._day_balance_weight
                    else best_fitness[2]
                ),
                selected_hard_conflict_count=selected_hard_conflict_count,
                combined_hard_conflict_count=combined_hard_conflict_count,
                cross_block_conflict_count=cross_block_conflict_count,
            ),
        )

    def _termination_reason(
        self,
        generation: int,
        best_fitness: FitnessValues,
        stagnant_generations: int,
    ) -> TerminationReason | None:
        # CHR-GA-006: feasibility is a phase, not a stop condition. A complete,
        # conflict-free schedule keeps evolving so P(C) can still improve it
        # (thesis p.40); elitism keeps the best feasible schedule.
        if generation >= self._config.max_generations:
            return TerminationReason.MAX_GENERATIONS
        if (
            self._config.stagnation_limit is not None
            and stagnant_generations >= self._config.stagnation_limit
        ):
            return TerminationReason.STAGNATION
        return None

    def _is_feasible(self, fitness: FitnessValues) -> bool:
        return fitness[0] == len(self._codec.offering_ids) and fitness[1] == 0

    def _next_generation(
        self,
        population: Sequence[list[ScheduleGene]],
        rng: Random,
    ) -> list[list[ScheduleGene]]:
        elites = self._elites(population)
        parents = [
            self._tournament(population, rng)
            for _ in range(self._config.population_size - len(elites))
        ]
        children = []
        for genes, parent in self._recombine(parents, rng):
            if rng.random() < self._config.mutation_rate:
                genes = tuple(mutate(genes, self._sampler, self._config, rng))
            # Repair is deterministic and idempotent, so a child identical to
            # an already repaired parent would come back unchanged; skip it.
            # Initial individuals are repaired only with
            # repair_initial_population, so a copy of an unrepaired one is
            # still repaired even when no operator changed a gene.
            needs_repair = genes != tuple(parent) or not getattr(
                parent, "repaired", False
            )
            child = self._individual_type(self._repair(genes) if needs_repair else genes)
            # Either just repaired or a copy of a repaired parent.
            child.repaired = True
            children.append(child)
        return elites + children

    def _repaired_individual(self, genes: Sequence[ScheduleGene]) -> list[ScheduleGene]:
        individual = self._individual_type(self._repair(genes))
        individual.repaired = True
        return individual

    def _repair(self, genes: Sequence[ScheduleGene]) -> tuple[ScheduleGene, ...]:
        result = self._repair_operator.repair(
            self._codec.decode(genes), self._repair_context
        )
        return self._codec.encode(result.candidate.assignments)

    def _recombine(
        self,
        parents: Sequence[Sequence[ScheduleGene]],
        rng: Random,
    ) -> list[tuple[tuple[ScheduleGene, ...], Sequence[ScheduleGene]]]:
        """Pair parents into offspring, each kept with the parent it came from."""
        offspring: list[tuple[tuple[ScheduleGene, ...], Sequence[ScheduleGene]]] = []
        for index in range(0, len(parents) - 1, 2):
            left, right = parents[index], parents[index + 1]
            children = (
                crossover(left, right, rng)
                if rng.random() < self._config.crossover_rate
                else (left, right)
            )
            offspring.extend(
                ((tuple(children[0]), left), (tuple(children[1]), right))
            )
        if len(parents) % 2:
            offspring.append((tuple(parents[-1]), parents[-1]))
        return offspring

    def _elites(
        self,
        population: Sequence[list[ScheduleGene]],
    ) -> list[list[ScheduleGene]]:
        if not self._config.elite_count:
            return []
        ranked = sorted(population, key=attrgetter("fitness"), reverse=True)
        # Individuals are never mutated in place - every operator returns a new
        # gene sequence - so an elite can be carried over by reference and keeps
        # its already-valid fitness.
        return list(ranked[: self._config.elite_count])

    def _tournament(
        self,
        population: Sequence[list[ScheduleGene]],
        rng: Random,
    ) -> list[ScheduleGene]:
        aspirants = [
            rng.choice(population) for _ in range(self._config.tournament_size)
        ]
        return max(aspirants, key=attrgetter("fitness"))

    def _evaluate_population(
        self,
        population: Sequence[list[ScheduleGene]],
    ) -> None:
        for individual in population:
            if not individual.fitness.valid:
                individual.fitness.values = self._evaluate(individual)

    def _best(self, population: Sequence[list[ScheduleGene]]) -> list[ScheduleGene]:
        return max(population, key=attrgetter("fitness"))

    def _reported_fitness(self, individual: list[ScheduleGene]) -> FitnessValues:
        """The fitness as recorded: the third value is historical attainment.

        With a day-balance weight the selection fitness adds the balance term
        to attainment; stored metrics keep reporting attainment alone so the
        recorded P(C) means the same in every run.
        """
        values = individual.fitness.values
        if not self._day_balance_weight:
            return values
        return (values[0], values[1], self._evaluate.historical_attainment(individual))

    def _record(
        self,
        generation: int,
        population: Sequence[list[ScheduleGene]],
        best: list[ScheduleGene],
    ) -> GenerationMetric:
        """Best fitness is the best seen so far; completeness is this population's."""
        offering_count = len(self._codec.offering_ids) or 1
        resolved_counts = [
            sum(1 for gene in individual if gene.is_resolved)
            for individual in population
        ]
        return GenerationMetric(
            generation=generation,
            best_fitness=self._reported_fitness(best),
            mean_assignment_completeness=(
                sum(resolved_counts) / (len(population) * offering_count)
            ),
        )
