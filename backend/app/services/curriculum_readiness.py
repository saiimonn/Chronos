"""Readiness warnings for Blocks that do not take what their curriculum requires.

This check runs beside `analyze_readiness`, not inside it. That function reads
the same `SchedulingProblem` the GA consumes, and the GA never reads a
curriculum; widening its input contract for data it ignores would let the two
drift for nothing. So the curriculum facts arrive here as `CohortCoverage`,
loaded separately, and the route reports them beside the blockers.

They are warnings, not blockers: a Block missing a required subject is still
something the GA can schedule, so a curriculum gap must never make a term
unready or disable generation.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.models.curriculum import MAX_TERM_NUMBER, MIN_TERM_NUMBER
from app.services.readiness import ReadinessBlocker


@dataclass(frozen=True, slots=True)
class CohortCoverage:
    """What one Block's curriculum requires this term against what it takes.

    `required_subject_codes` is None when no curriculum covers the cohort. That
    is kept apart from an empty set, which means the curriculum was found and
    requires nothing this term, so an unresolved Block is never reported as
    missing every subject.

    `entry_year` is None when the term's `academic_year` cannot be read as
    "YYYY-YYYY", so no curriculum lookup was possible at all. A `term_number`
    outside the range a curriculum records likewise leaves
    `required_subject_codes` None rather than empty.
    """

    block_id: UUID
    block_name: str
    program_code: str
    academic_year: str
    term_number: int
    entry_year: str | None
    required_subject_codes: frozenset[str] | None
    taken_subject_codes: frozenset[str]


def analyze_curriculum_coverage(
    cohorts: Sequence[CohortCoverage],
) -> tuple[ReadinessBlocker, ...]:
    """One warning per Block that lacks a curriculum or a required subject.

    A Block whose term has an unreadable academic year gets its own warning:
    no entry year exists to name, and "no curriculum" would send the user to
    the curriculum records when the term record is at fault. A term number no
    curriculum can record is reported the same way, for the same reason.

    Warnings share the blocker's shape so the report renders both the same way.

    Subjects a Block takes beyond its curriculum, such as electives, are not
    checked: the curriculum is a floor, not a whitelist.
    """

    warnings: list[ReadinessBlocker] = []
    for cohort in cohorts:
        if cohort.entry_year is None:
            warnings.append(
                ReadinessBlocker(
                    "COHORT_ENTRY_YEAR_UNREADABLE",
                    f"{cohort.block_name} has no curriculum because the term's "
                    f"academic year {cohort.academic_year!r} is not in "
                    f"YYYY-YYYY format.",
                    {
                        "block_id": str(cohort.block_id),
                        "block_name": cohort.block_name,
                        "academic_year": cohort.academic_year,
                    },
                )
            )
            continue
        if not MIN_TERM_NUMBER <= cohort.term_number <= MAX_TERM_NUMBER:
            warnings.append(
                ReadinessBlocker(
                    "COHORT_TERM_OUTSIDE_CURRICULUM",
                    f"{cohort.block_name} has no curriculum requirements because "
                    f"term {cohort.term_number} is outside the curriculum's "
                    f"terms {MIN_TERM_NUMBER}-{MAX_TERM_NUMBER}.",
                    {
                        "block_id": str(cohort.block_id),
                        "block_name": cohort.block_name,
                        "term_number": cohort.term_number,
                    },
                )
            )
            continue
        if cohort.required_subject_codes is None:
            warnings.append(
                ReadinessBlocker(
                    "COHORT_WITHOUT_CURRICULUM",
                    f"{cohort.block_name} has no curriculum for "
                    f"{cohort.program_code} entry year {cohort.entry_year}.",
                    {
                        "block_id": str(cohort.block_id),
                        "block_name": cohort.block_name,
                        "program_code": cohort.program_code,
                        "entry_year": cohort.entry_year,
                    },
                )
            )
            continue
        missing = sorted(cohort.required_subject_codes - cohort.taken_subject_codes)
        if missing:
            warnings.append(
                ReadinessBlocker(
                    "COHORT_MISSING_REQUIRED_SUBJECT",
                    f"{cohort.block_name} does not take required subjects: "
                    f"{', '.join(missing)}.",
                    {
                        "block_id": str(cohort.block_id),
                        "block_name": cohort.block_name,
                        "missing_subject_codes": missing,
                    },
                )
            )
    return tuple(warnings)


__all__ = ["CohortCoverage", "analyze_curriculum_coverage"]
