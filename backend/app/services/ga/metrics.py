"""CHR-29 / CHR-GA-002 run metrics for one genetic-algorithm execution.

These records carry the FR-GEN-007 and FR-GEN-008 reporting fields - seed,
termination generation, execution time, completeness, hard conflicts, and
historical attainment. They are returned to the caller rather than written to
the database: the job and schedule tables that own persistence arrive with
CHR-WORKER-001 and CHR-SCH-001.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain import CandidateSchedule
from app.services.ga.config import GAConfig
from app.services.ga.fitness import FitnessValues


class TerminationReason(StrEnum):
    MAX_GENERATIONS = "MAX_GENERATIONS"
    # Legacy: no longer produced since CHR-GA-006; kept so stored runs parse.
    OPTIMAL_REACHED = "OPTIMAL_REACHED"
    STAGNATION = "STAGNATION"


@dataclass(frozen=True, slots=True)
class GenerationMetric:
    """One evaluated generation; generation 0 is the initial population."""

    generation: int
    best_fitness: FitnessValues
    mean_assignment_completeness: float


@dataclass(frozen=True, slots=True)
class GARunMetrics:
    random_seed: int
    config: GAConfig
    termination_reason: TerminationReason
    termination_generation: int
    elapsed_seconds: float
    generations: tuple[GenerationMetric, ...]
    required_offering_count: int
    assigned_offering_count: int
    required_class_count: int
    assigned_class_count: int
    hard_conflict_count: int
    historical_pattern_attainment: float
    selected_hard_conflict_count: int | None = None
    combined_hard_conflict_count: int | None = None
    cross_block_conflict_count: int | None = None
    first_feasible_generation: int | None = None

    @property
    def offering_completeness(self) -> float:
        if self.required_offering_count == 0:
            return 0.0
        return self.assigned_offering_count / self.required_offering_count

    @property
    def assignment_completeness(self) -> float:
        if self.required_class_count == 0:
            return 0.0
        return self.assigned_class_count / self.required_class_count


@dataclass(frozen=True, slots=True)
class GARunResult:
    candidate: CandidateSchedule
    metrics: GARunMetrics
