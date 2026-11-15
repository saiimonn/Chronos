"""Resolving the prospectus that governs a cohort, and reading what it requires.

Resolution is split in two on purpose. `select_effective_year` is pure — it
takes the years a program has on file and answers which one applies — so the
cohort rule can be asserted against fixed inputs. The session-bound functions
here do nothing but fetch and delegate.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import entry_academic_year
from app.models import (
    BlockSection,
    BlockSectionOffering,
    Curriculum,
    CurriculumRequirement,
    Term,
)
from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL
from app.models.curriculum import MAX_TERM_NUMBER, MIN_TERM_NUMBER
from app.services.curriculum_readiness import CohortCoverage


class NoApplicableCurriculumError(LookupError):
    """No curriculum on file for a program covers a cohort's entry year.

    Raised rather than returned so a caller cannot read an empty subject list
    as "this cohort requires nothing". Readiness catches it and reports a
    warning; the Block can still be scheduled.
    """

    def __init__(self, program_code: str, entry_year: str) -> None:
        super().__init__(
            f"no curriculum for program {program_code!r} "
            f"covers entry year {entry_year!r}"
        )
        self.program_code = program_code
        self.entry_year = entry_year


def select_effective_year(
    available_years: Iterable[str], entry_year: str
) -> str | None:
    """The effective year that governs a cohort entering in `entry_year`.

    An exact match wins. Otherwise the most recent year on or before entry
    applies, because a prospectus stays in force until a later one replaces it
    — a department that revised its curriculum twice in eight years has two
    rows, not eight. Returns None when every year on file is later than entry,
    which means the cohort predates anything recorded.

    Comparison is lexicographic, which is correct only because the stored
    format is fixed-width "YYYY-YYYY"; a check constraint on the column keeps
    it that way.
    """

    applicable = [year for year in available_years if year <= entry_year]
    return max(applicable, default=None)


def resolve_for_cohort(
    db: Session, program_code: str, year_level: int, academic_year: str
) -> Curriculum:
    """The curriculum a cohort at `year_level` during `academic_year` follows.

    Raises `NoApplicableCurriculumError` when nothing on file covers it.
    """

    entry_year = entry_academic_year(academic_year, year_level)
    # Every curriculum for the program is loaded rather than filtered in SQL so
    # the choice runs through `select_effective_year` and the cohort rule exists
    # once. A program carries a handful of revisions, not a table's worth.
    candidates = {
        curriculum.effective_year: curriculum
        for curriculum in db.scalars(
            select(Curriculum).where(Curriculum.program_code == program_code)
        ).all()
    }
    effective_year = select_effective_year(candidates, entry_year)
    if effective_year is None:
        raise NoApplicableCurriculumError(program_code, entry_year)
    return candidates[effective_year]


def resolve_for_block(db: Session, block: BlockSection) -> Curriculum:
    """The curriculum a Block follows, read through the Term it belongs to."""

    # `db.get` rather than `block.term` so a Block that has not been flushed
    # resolves the same way as one read back from the database.
    term = db.get(Term, block.term_id)
    return resolve_for_cohort(
        db, block.program_code, block.year_level, term.academic_year
    )


def governing_curriculum_ids(db: Session, academic_year: str) -> set[UUID]:
    """The curricula some cohort enrolled during `academic_year` follows.

    Every program and supported year level resolves through `resolve_for_cohort`,
    so an older prospectus stays in the set while a cohort still follows it and
    drops out once every cohort has moved to its replacement.
    """

    program_codes = db.scalars(select(Curriculum.program_code).distinct()).all()
    governing: set[UUID] = set()
    for program_code in program_codes:
        for year_level in range(MIN_YEAR_LEVEL, MAX_YEAR_LEVEL + 1):
            try:
                curriculum = resolve_for_cohort(
                    db, program_code, year_level, academic_year
                )
            except (NoApplicableCurriculumError, ValueError):
                continue
            governing.add(curriculum.curriculum_id)
    return governing


def required_subject_codes(
    db: Session, curriculum: Curriculum, year_level: int, term_number: int
) -> list[str]:
    """The subjects one year level of a curriculum must take in one term.

    Empty is a legitimate answer: a curriculum that records nothing for a term
    is different from one that could not be found, which raises instead.
    """

    return list(
        db.scalars(
            select(CurriculumRequirement.subject_code)
            .where(
                CurriculumRequirement.curriculum_id == curriculum.curriculum_id,
                CurriculumRequirement.year_level == year_level,
                CurriculumRequirement.term_number == term_number,
            )
            .order_by(CurriculumRequirement.subject_code)
        ).all()
    )


def load_cohort_coverage(db: Session, term_id: UUID) -> list[CohortCoverage]:
    """Each Block in a term, with what its curriculum requires and what it takes.

    A Block whose curriculum cannot be resolved is recorded as a coverage fact,
    with no required codes, rather than raised: readiness reports it as a
    warning beside every other Block instead of failing the whole report. That
    includes a term whose academic year is not "YYYY-YYYY": the term schema
    does not enforce the format, so an unreadable year is a data fault to
    report, not an error to raise. The same holds for a term number outside
    the range a curriculum can record: no requirement can exist for it, so an
    empty lookup would read as "requires nothing" when it means "cannot say".
    """

    term = db.get(Term, term_id)
    if term is None:
        return []

    taken: dict[UUID, set[str]] = {}
    for block_id, subject_code in db.execute(
        select(BlockSectionOffering.block_id, BlockSectionOffering.subject_code)
        .where(BlockSectionOffering.term_id == term_id)
    ):
        taken.setdefault(block_id, set()).add(subject_code)

    blocks = db.scalars(
        select(BlockSection)
        .where(BlockSection.term_id == term_id)
        .order_by(
            BlockSection.program_code,
            BlockSection.year_level,
            BlockSection.section_letter,
        )
    ).all()

    # Parallel Blocks of one cohort share a curriculum, so each program and
    # year level resolves once.
    required_by_cohort: dict[tuple[str, int], frozenset[str] | None] = {}
    coverage: list[CohortCoverage] = []
    term_in_curriculum_range = (
        MIN_TERM_NUMBER <= term.term_number <= MAX_TERM_NUMBER
    )
    for block in blocks:
        cohort = (block.program_code, block.year_level)
        try:
            entry_year = entry_academic_year(term.academic_year, block.year_level)
        except ValueError:
            entry_year = None
            required_by_cohort[cohort] = None
        if not term_in_curriculum_range:
            required_by_cohort[cohort] = None
        if cohort not in required_by_cohort:
            try:
                curriculum = resolve_for_cohort(
                    db, block.program_code, block.year_level, term.academic_year
                )
            except NoApplicableCurriculumError:
                required_by_cohort[cohort] = None
            else:
                required_by_cohort[cohort] = frozenset(
                    required_subject_codes(
                        db, curriculum, block.year_level, term.term_number
                    )
                )
        coverage.append(
            CohortCoverage(
                block_id=block.block_id,
                block_name=block.name,
                program_code=block.program_code,
                academic_year=term.academic_year,
                term_number=term.term_number,
                entry_year=entry_year,
                required_subject_codes=required_by_cohort[cohort],
                taken_subject_codes=frozenset(taken.get(block.block_id, ())),
            )
        )
    return coverage
