import re
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    BlockSection,
    HistoricalSnapshot,
    Schedule,
    SubjectOffering,
    Term,
    TimeSlot,
    rule_set_term,
)
from app.schemas.term import TermCreate, TermUpdate
from app.services.catalog.guards import count_where, delete_guarded, lock_term
from app.services.catalog.time_slots import ensure_canonical_slots

# Terms before this academic year were scheduled before Chronos and are done:
# they can still exist (historical import and reference onboarding write them
# directly), but the setup paths below refuse to create, rename to, or
# activate them. The frontend mirrors this value in SchedulePage.
SETUP_CUTOFF_ACADEMIC_YEAR = "2026-2027"

_ACADEMIC_YEAR = re.compile(r"(\d{4})-(\d{4})")


def _start_year(academic_year: str) -> int:
    return int(academic_year[:4])


def _validate_academic_year(academic_year: str) -> None:
    match = _ACADEMIC_YEAR.fullmatch(academic_year)
    if match is None or int(match.group(2)) != int(match.group(1)) + 1:
        raise APIError(
            422,
            "INVALID_ACADEMIC_YEAR",
            "Academic year must be two consecutive years, like 2026-2027.",
            {"academic_year": academic_year},
        )


def is_before_setup_cutoff(academic_year: str) -> bool:
    """True for a period older than the first one Chronos sets up."""
    if _ACADEMIC_YEAR.fullmatch(academic_year) is None:
        return False
    return _start_year(academic_year) < _start_year(SETUP_CUTOFF_ACADEMIC_YEAR)


def _refuse_before_cutoff(academic_year: str) -> None:
    if is_before_setup_cutoff(academic_year):
        raise APIError(
            409,
            "TERM_BEFORE_SETUP_CUTOFF",
            f"Terms before AY {SETUP_CUTOFF_ACADEMIC_YEAR} are already done "
            "and cannot be set up.",
            {
                "academic_year": academic_year,
                "cutoff_academic_year": SETUP_CUTOFF_ACADEMIC_YEAR,
            },
        )


def _refuse_duplicate(
    db: Session, academic_year: str, term_number: int, exclude: UUID | None = None
) -> None:
    stmt = select(Term).where(
        Term.academic_year == academic_year, Term.term_number == term_number
    )
    if exclude is not None:
        stmt = stmt.where(Term.term_id != exclude)
    existing = db.scalar(stmt.limit(1))
    if existing is None:
        return
    message = f"Term {term_number} of AY {academic_year} already exists."
    if existing.is_archived:
        message += " It is archived; restore it instead."
    raise APIError(
        409,
        "DUPLICATE_RESOURCE",
        message,
        {
            "conflict": {
                "academic_year": academic_year,
                "term_number": str(term_number),
            },
            "term_id": str(existing.term_id),
            "is_archived": existing.is_archived,
        },
    )


def list_terms(db: Session, include_archived: bool = False) -> list[Term]:
    stmt = select(Term).order_by(Term.academic_year, Term.term_number)
    if not include_archived:
        stmt = stmt.where(Term.is_archived.is_(False))
    return list(db.scalars(stmt).all())


def get_term(db: Session, term_id: UUID) -> Term:
    term = db.get(Term, term_id)
    if term is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    return term


def create_term(db: Session, payload: TermCreate) -> Term:
    _validate_academic_year(payload.academic_year)
    _refuse_before_cutoff(payload.academic_year)
    _refuse_duplicate(db, payload.academic_year, payload.term_number)
    # A new term is never active; selection is a separate, explicit step.
    term = Term(
        academic_year=payload.academic_year,
        term_number=payload.term_number,
        is_active=False,
    )
    db.add(term)
    # Flush to obtain the generated term_id, then build the grid in the same
    # transaction: a term without its canonical slots is not a state any caller
    # should be able to observe.
    db.flush()
    ensure_canonical_slots(db, term.term_id)
    db.commit()
    db.refresh(term)
    return term


def _is_captured(db: Session, term: Term) -> bool:
    """True when an immutable historical snapshot exists for this term."""
    return (
        db.scalar(
            select(HistoricalSnapshot.snapshot_id)
            .where(
                or_(
                    HistoricalSnapshot.source_term_id == term.term_id,
                    (HistoricalSnapshot.academic_year == term.academic_year)
                    & (HistoricalSnapshot.term_number == term.term_number),
                )
            )
            .limit(1)
        )
        is not None
    )


def captured_term_ids(db: Session, terms: list[Term]) -> set[UUID]:
    """Ids of `terms` that are captured, from one query (same rule as `_is_captured`)."""
    snapshots = db.execute(
        select(
            HistoricalSnapshot.source_term_id,
            HistoricalSnapshot.academic_year,
            HistoricalSnapshot.term_number,
        )
    ).all()
    source_ids = {row.source_term_id for row in snapshots}
    periods = {(row.academic_year, row.term_number) for row in snapshots}
    return {
        t.term_id
        for t in terms
        if t.term_id in source_ids or (t.academic_year, t.term_number) in periods
    }


def update_term(db: Session, term_id: UUID, payload: TermUpdate) -> Term:
    # Lock like capture does, so a PATCH racing an in-flight capture waits for
    # its commit and then sees the snapshot.
    term = lock_term(db, term_id)
    academic_year = payload.academic_year or term.academic_year
    term_number = payload.term_number or term.term_number
    if (academic_year, term_number) != (term.academic_year, term.term_number):
        _refuse_if_archived(term)
        if _is_captured(db, term):
            raise APIError(
                409,
                "TERM_ARCHIVED",
                "A captured historical term cannot be renamed.",
            )
        if db.scalar(
            select(HistoricalSnapshot.snapshot_id)
            .where(
                HistoricalSnapshot.academic_year == academic_year,
                HistoricalSnapshot.term_number == term_number,
            )
            .limit(1)
        ):
            raise APIError(
                409, "TERM_ARCHIVED", "This period already has captured history."
            )
        _validate_academic_year(academic_year)
        _refuse_before_cutoff(academic_year)
        _refuse_duplicate(db, academic_year, term_number, exclude=term.term_id)
    term.academic_year = academic_year
    term.term_number = term_number
    db.commit()
    db.refresh(term)
    return term


def delete_term(db: Session, term_id: UUID) -> None:
    """Remove a term and the grid it was created with.

    No route exposes this: operators archive terms instead (`archive_term`),
    so nothing that references a term can go missing. It is kept for
    maintenance scripts and for the delete-restrict regression tests.

    Time slots are not counted as a dependent. They are generated from the
    calendar when the term is created rather than entered by anyone, so a grid
    is part of the term and not a reason to refuse to delete it. A term that was
    actually scheduled is still refused: its placements hang off `schedules`,
    which is checked below, and the slot foreign key restricts on top of that.
    """

    term = lock_term(db, term_id)
    delete_guarded(
        db,
        term,
        [
            (
                "block_sections",
                count_where(BlockSection, BlockSection.term_id == term_id),
            ),
            ("schedules", count_where(Schedule, Schedule.term_id == term_id)),
            (
                "subject_offerings",
                count_where(SubjectOffering, SubjectOffering.term_id == term_id),
            ),
            (
                "rule_sets",
                count_where(rule_set_term, rule_set_term.c.term_id == term_id),
            ),
            (
                "historical_snapshots",
                count_where(
                    HistoricalSnapshot, HistoricalSnapshot.source_term_id == term_id
                ),
            ),
        ],
        before_delete=lambda: db.execute(
            delete(TimeSlot).where(TimeSlot.term_id == term_id)
        ),
    )


def _refuse_if_archived(term: Term) -> None:
    if term.is_archived:
        raise APIError(
            409,
            "TERM_SOFT_ARCHIVED",
            "This term is archived. Restore it before changing or selecting it.",
        )


def archive_term(db: Session, term_id: UUID) -> Term:
    """Hide a term from setup without deleting it.

    Terms are never hard-deleted: Blocks, schedules, rule sets and snapshots
    may reference them, so archiving only flips a flag and restore undoes it.
    The active term is refused because hiding it would leave the setup page
    working on a term nobody can see; captured terms are refused because they
    are already read-only history and the History page lists them.
    """
    term = lock_term(db, term_id)
    if term.is_active:
        raise APIError(
            409,
            "TERM_ACTIVE",
            "The active term cannot be archived. Select another term first.",
        )
    if _is_captured(db, term):
        raise APIError(
            409,
            "TERM_ARCHIVED",
            "A captured historical term is already read-only history.",
        )
    term.is_archived = True
    db.commit()
    db.refresh(term)
    return term


def restore_term(db: Session, term_id: UUID) -> Term:
    term = lock_term(db, term_id)
    term.is_archived = False
    db.commit()
    db.refresh(term)
    return term


def select_term(db: Session, term_id: UUID) -> Term:
    """Make `term_id` the one active term.

    The incumbent is locked first so two concurrent selections serialize, and
    clearing before setting keeps the partial unique index
    `uq_term_single_active` satisfied at every statement boundary.
    """
    target = get_term(db, term_id)
    incumbent = db.scalar(select(Term).where(Term.is_active).with_for_update())
    target = db.scalar(
        select(Term)
        .where(Term.term_id == term_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    if _is_captured(db, target):
        raise APIError(
            409,
            "TERM_ARCHIVED",
            "A captured historical term cannot be selected for operational scheduling.",
        )
    _refuse_if_archived(target)
    # Re-selecting the term that is already active stays idempotent, so a
    # legacy active term from before the cutoff does not lock the page.
    if not target.is_active:
        _refuse_before_cutoff(target.academic_year)
    if incumbent is not None and incumbent.term_id != term_id:
        incumbent.is_active = False
        db.flush()
    target.is_active = True
    db.commit()
    db.refresh(target)
    return target
