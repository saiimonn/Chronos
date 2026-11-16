"""CHR-28 / CHR-GA-001 strict lexicographic fitness evaluation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol
from uuid import UUID

from app.domain import Assignment, LockedAssignment, SchedulingProblem
from app.services.ga.chromosome import ChromosomeCodec, ScheduleGene
from app.services.ga.day_balance import day_balance_score
from app.services.validation import ConstraintEngine


FitnessValues = tuple[int, int, float]


@dataclass(frozen=True, slots=True)
class HistoricalScoreBreakdown:
    """Four visible stream scores; None means that stream was not applicable."""

    teacher_subject_score: float | None
    teacher_day_score: float | None
    teacher_time_score: float | None
    teacher_subject_room_score: float | None
    historical_pattern_attainment: float

    def __post_init__(self) -> None:
        values = (
            self.teacher_subject_score,
            self.teacher_day_score,
            self.teacher_time_score,
            self.teacher_subject_room_score,
            self.historical_pattern_attainment,
        )
        if any(value is not None and not isfinite(value) for value in values):
            raise ValueError("historical scores must be finite")


class HistoricalAttainmentScorer(Protocol):
    def score(
        self,
        problem: SchedulingProblem,
        assignments: Sequence[Assignment],
    ) -> HistoricalScoreBreakdown: ...


class ZeroHistoricalAttainmentScorer:
    """Explicit unguided scoring without inventing historical evidence."""

    def score(
        self,
        problem: SchedulingProblem,
        assignments: Sequence[Assignment],
    ) -> HistoricalScoreBreakdown:
        return HistoricalScoreBreakdown(
            teacher_subject_score=None,
            teacher_day_score=None,
            teacher_time_score=None,
            teacher_subject_room_score=None,
            historical_pattern_attainment=0.0,
        )


class LexicographicFitnessEvaluator:
    """Evaluate offering completeness, hard conflicts, then the soft score.

    The first component counts resolved offering genes (not block-level
    assignments), so the GA terminates when every offering has a placement.

    The soft score is historical attainment alone unless ``day_balance_weight``
    is set; then it adds that weight times the day-balance score (how evenly
    each Block's hours spread over Monday-Thursday). Thesis evaluation runs
    leave the weight at 0, so their third component stays pure attainment.
    """

    def __init__(
        self,
        problem: SchedulingProblem,
        historical_scorer: HistoricalAttainmentScorer | None = None,
        constraint_engine: ConstraintEngine | None = None,
        locked_assignments: Sequence[LockedAssignment] | None = None,
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
        day_balance_weight: float = 0.0,
    ):
        if not isfinite(day_balance_weight) or day_balance_weight < 0:
            raise ValueError("day_balance_weight must be a finite, non-negative number")
        self._day_balance_weight = day_balance_weight
        self._problem = problem
        self._codec = ChromosomeCodec(problem)
        self._historical_scorer = (
            historical_scorer or ZeroHistoricalAttainmentScorer()
        )
        self._constraint_engine = constraint_engine or ConstraintEngine()
        self._locked_assignments = (
            tuple(locked_assignments) if locked_assignments else ()
        )
        self._offering_block_ids = offering_block_ids

    def __call__(self, individual: Sequence[ScheduleGene]) -> FitnessValues:
        candidate = self._codec.decode(individual)
        report = self._constraint_engine.evaluate(
            self._problem,
            candidate.assignments,
            locked_assignments=self._locked_assignments,
            offering_block_ids=self._offering_block_ids,
        )
        historical_scores = self._historical_scorer.score(
            self._problem,
            candidate.assignments,
        )
        resolved_offerings = sum(
            1 for gene in individual if gene.is_resolved
        )
        soft = historical_scores.historical_pattern_attainment
        if self._day_balance_weight:
            soft += self._day_balance_weight * day_balance_score(
                self._problem, candidate.assignments
            )
        return (
            resolved_offerings,
            -report.hard_conflict_count,
            soft,
        )

    def historical_attainment(self, individual: Sequence[ScheduleGene]) -> float:
        """Historical attainment alone, without the day-balance term."""
        return self._historical_scorer.score(
            self._problem, self._codec.decode(individual).assignments
        ).historical_pattern_attainment
