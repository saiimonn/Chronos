"""CHR-29 / CHR-GA-002 bounded genetic-algorithm run parameters."""

from dataclasses import dataclass

from app.domain.meeting_patterns import SEMESTER_MEETING_PATTERNS
from app.services.validation.engine import ConstraintEngine


def validate_meeting_day_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    """CHR-125 / CHR-CFG-002: only the semester patterns MW, TTh and F.

    The summer term (term 3) always meets MTWTh, so it is not configured. A
    pattern string such as "MWTTh" parses cleanly into four days, so the set
    is closed rather than checked token by token.
    """
    if not patterns or any(not pattern for pattern in patterns):
        raise ValueError("meeting_day_patterns must not be empty")
    for pattern in patterns:
        ConstraintEngine.parse_teaching_days(pattern)
        if pattern not in SEMESTER_MEETING_PATTERNS:
            raise ValueError(
                "meeting_day_patterns may only contain MW, TTh or F, "
                f"not {pattern!r}"
            )
    return patterns


@dataclass(frozen=True, slots=True)
class GAConfig:
    """Operator and termination parameters validated against FR-CFG-002 ranges."""

    population_size: int = 50
    max_generations: int = 100
    crossover_rate: float = 0.8
    mutation_rate: float = 0.2
    gene_mutation_rate: float = 0.1
    tournament_size: int = 3
    elite_count: int = 1
    stagnation_limit: int | None = 20
    random_seed: int = 0
    meeting_day_patterns: tuple[str, ...] = ("MW", "TTh", "F")

    def __post_init__(self) -> None:
        if self.population_size <= 0:
            raise ValueError("population_size must be greater than zero")
        if self.max_generations <= 0:
            raise ValueError("max_generations must be greater than zero")
        if not 0.0 <= self.crossover_rate <= 1.0:
            raise ValueError("crossover_rate must be between 0.0 and 1.0")
        if not 0.0 <= self.mutation_rate <= 1.0:
            raise ValueError("mutation_rate must be between 0.0 and 1.0")
        if not 0.0 <= self.gene_mutation_rate <= 1.0:
            raise ValueError("gene_mutation_rate must be between 0.0 and 1.0")
        if self.tournament_size < 2:
            raise ValueError("tournament_size must be at least 2")
        if self.tournament_size > self.population_size:
            raise ValueError("tournament_size must not exceed population_size")
        if self.elite_count < 0:
            raise ValueError("elite_count must not be negative")
        if self.elite_count >= self.population_size:
            raise ValueError("elite_count must be smaller than population_size")
        if self.stagnation_limit is not None and self.stagnation_limit <= 0:
            raise ValueError("stagnation_limit must be greater than zero")
        if not self.meeting_day_patterns or any(
            not pattern for pattern in self.meeting_day_patterns
        ):
            raise ValueError("meeting_day_patterns must not be empty")
