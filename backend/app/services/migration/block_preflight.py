"""What the CHR-BLK-002 cutover refuses to guess.

A Block is a cohort — program, year level, section letter — so the course
sections of one cohort collapse into a single row, and the subject each of them
named moves onto a link to a Subject Offering. Two things about that mapping are
not determined by the old rows: which offering a section belongs to when its
subject has several, and whether the resulting links stay inside the LAB/LEC
block limit. Two more are determined but lossy: capacity is fixed at twenty
(twenty-five expanded), so a larger enrollment has nowhere to go, and a cohort
whose sections disagree on enrollment has no single answer.

Detection is a pure function over rows. The command that runs it supplies the
queries and nothing else, so every case below is stated as data rather than as
a database fixture, and the migration re-asserts the same invariants itself.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain import SubjectType
from app.models.block_section import BLOCK_BASE_CAPACITY, BLOCK_EXPANSION_CAPACITY

MAX_BLOCK_CAPACITY = BLOCK_BASE_CAPACITY + BLOCK_EXPANSION_CAPACITY


class DefectKind(StrEnum):
    MISSING_OFFERING = "missing_offering"
    AMBIGUOUS_OFFERING = "ambiguous_offering"
    BLOCK_LIMIT_EXCEEDED = "block_limit_exceeded"
    ENROLLMENT_ABOVE_CAPACITY = "enrollment_above_capacity"
    ENROLLMENT_DISAGREEMENT = "enrollment_disagreement"
    SECTION_LETTER_CASE_COLLISION = "section_letter_case_collision"


# The only defect the command can resolve on its own: a subject that no offering
# covers gets one created at the next group number, through the same service the
# API uses. Everything else needs a person to decide.
FIXABLE_KINDS = frozenset({DefectKind.MISSING_OFFERING})


@dataclass(frozen=True, slots=True)
class LegacySection:
    """One `course_section` row as it stands before the cutover."""

    section_id: UUID
    term_id: UUID
    subject_code: str
    program_code: str
    year_level: int
    section_letter: str
    expected_enrollment: int

    @property
    def cohort(self) -> tuple[UUID, str, int, str]:
        """The Block this row collapses into, keyed the way the Block is."""
        return (
            self.term_id,
            self.program_code.upper(),
            self.year_level,
            self.section_letter.upper(),
        )


@dataclass(frozen=True, slots=True)
class Defect:
    kind: DefectKind
    term_id: UUID
    subject_code: str | None
    cohort: tuple[UUID, str, int, str] | None
    detail: str
    section_ids: tuple[UUID, ...]
    schedule_record_count: int

    @property
    def is_fixable(self) -> bool:
        return self.kind in FIXABLE_KINDS


def _sort_key(defect: Defect) -> tuple:
    return (
        str(defect.term_id),
        defect.subject_code or "",
        str(defect.cohort or ""),
        defect.kind,
    )


def find_defects(
    sections: Sequence[LegacySection],
    *,
    offering_counts: Mapping[tuple[UUID, str], int],
    subject_types: Mapping[str, SubjectType],
    schedule_counts: Mapping[UUID, int],
) -> list[Defect]:
    """Every reason the cutover cannot proceed, ordered so reruns match.

    `offering_counts` holds how many Subject Offerings exist per term and
    subject, `subject_types` the LAB/LEC type of every subject a section names,
    and `schedule_counts` how many schedule records point at each section — the
    last only so a report can say what a defect is holding up.
    """

    defects: list[Defect] = []
    defects.extend(_offering_defects(sections, offering_counts, schedule_counts))
    defects.extend(_block_limit_defects(sections, subject_types, schedule_counts))
    defects.extend(_capacity_defects(sections, schedule_counts))
    defects.extend(_letter_case_defects(sections, schedule_counts))
    return sorted(defects, key=_sort_key)


def _grouped(
    sections: Sequence[LegacySection], key
) -> dict[object, list[LegacySection]]:
    groups: dict[object, list[LegacySection]] = {}
    for section in sections:
        groups.setdefault(key(section), []).append(section)
    return groups


def _scheduled(
    sections: Sequence[LegacySection], schedule_counts: Mapping[UUID, int]
) -> int:
    return sum(schedule_counts.get(section.section_id, 0) for section in sections)


def _offering_defects(sections, offering_counts, schedule_counts) -> list[Defect]:
    defects = []
    for (term_id, subject_code), rows in _grouped(
        sections, lambda row: (row.term_id, row.subject_code)
    ).items():
        count = offering_counts.get((term_id, subject_code), 0)
        if count == 1:
            continue
        if count == 0:
            kind = DefectKind.MISSING_OFFERING
            detail = "no Subject Offering exists for this term and subject"
        else:
            kind = DefectKind.AMBIGUOUS_OFFERING
            detail = (
                f"{count} Subject Offerings exist for this term and subject; "
                "which one each cohort takes is not recorded"
            )
        defects.append(
            Defect(
                kind=kind,
                term_id=term_id,
                subject_code=subject_code,
                cohort=None,
                detail=detail,
                section_ids=tuple(row.section_id for row in rows),
                schedule_record_count=_scheduled(rows, schedule_counts),
            )
        )
    return defects


def _block_limit_defects(sections, subject_types, schedule_counts) -> list[Defect]:
    defects = []
    for (term_id, subject_code), rows in _grouped(
        sections, lambda row: (row.term_id, row.subject_code)
    ).items():
        # A missing type is a broken read, not a migration decision: every
        # section names a subject through a foreign key.
        limit = subject_types[subject_code].max_blocks_per_offering
        cohorts = {row.cohort for row in rows}
        if len(cohorts) <= limit:
            continue
        defects.append(
            Defect(
                kind=DefectKind.BLOCK_LIMIT_EXCEEDED,
                term_id=term_id,
                subject_code=subject_code,
                cohort=None,
                detail=(
                    f"{len(cohorts)} cohorts take this subject but a "
                    f"{subject_types[subject_code]} offering serves at most "
                    f"{limit}; split them across further groups first"
                ),
                section_ids=tuple(row.section_id for row in rows),
                schedule_record_count=_scheduled(rows, schedule_counts),
            )
        )
    return defects


def _capacity_defects(sections, schedule_counts) -> list[Defect]:
    defects = []
    for cohort, rows in _grouped(sections, lambda row: row.cohort).items():
        enrollments = {row.expected_enrollment for row in rows}
        largest = max(enrollments)
        if largest > MAX_BLOCK_CAPACITY:
            defects.append(
                Defect(
                    kind=DefectKind.ENROLLMENT_ABOVE_CAPACITY,
                    term_id=cohort[0],
                    subject_code=None,
                    cohort=cohort,
                    detail=(
                        f"expected enrollment {largest} exceeds the "
                        f"{MAX_BLOCK_CAPACITY} seats an expanded Block holds"
                    ),
                    section_ids=tuple(row.section_id for row in rows),
                    schedule_record_count=_scheduled(rows, schedule_counts),
                )
            )
        elif len(enrollments) > 1:
            defects.append(
                Defect(
                    kind=DefectKind.ENROLLMENT_DISAGREEMENT,
                    term_id=cohort[0],
                    subject_code=None,
                    cohort=cohort,
                    detail=(
                        "sections of this cohort disagree on expected "
                        f"enrollment ({sorted(enrollments)}); one Block cannot "
                        "carry both"
                    ),
                    section_ids=tuple(row.section_id for row in rows),
                    schedule_record_count=_scheduled(rows, schedule_counts),
                )
            )
    return defects


def _letter_case_defects(sections, schedule_counts) -> list[Defect]:
    defects = []
    for cohort, rows in _grouped(sections, lambda row: row.cohort).items():
        letters = {row.section_letter for row in rows}
        if len(letters) == 1:
            continue
        defects.append(
            Defect(
                kind=DefectKind.SECTION_LETTER_CASE_COLLISION,
                term_id=cohort[0],
                subject_code=None,
                cohort=cohort,
                detail=(
                    f"section letters {sorted(letters)} differ only by case and "
                    "would collapse into one Block"
                ),
                section_ids=tuple(row.section_id for row in rows),
                schedule_record_count=_scheduled(rows, schedule_counts),
            )
        )
    return defects
