from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    BlockSection,
    BlockSectionOffering,
    Curriculum,
    HistoricalSnapshot,
    Room,
    Schedule,
    ScheduleRecord,
    SubjectOffering,
    Term,
)

DEPENDENT_LABEL_LIMIT = 10


def get_active_term(db: Session) -> Term:
    """Return the one active term, or refuse the request.

    Term selection is what makes a term writable, so every term-scoped
    operation resolves the active term here rather than trusting a client to
    say which term it meant.
    """
    term = db.scalar(select(Term).where(Term.is_active))
    if term is None:
        raise APIError(
            409,
            "NO_ACTIVE_TERM",
            "No academic term is currently active.",
        )
    return term


def require_active_term(db: Session, term_id: UUID) -> Term:
    term = get_active_term(db)
    if term.term_id != term_id:
        raise APIError(
            409,
            "TERM_NOT_ACTIVE",
            "Only the active academic term may be modified.",
            {
                "active_term_id": str(term.term_id),
                "requested_term_id": str(term_id),
            },
        )
    return term


def lock_term(db: Session, term_id: UUID) -> Term:
    """Serialize grid writers before taking any child or availability row locks."""
    # FOR NO KEY UPDATE conflicts with review's FOR SHARE, but allows FK checks.
    term = db.scalar(
        select(Term).where(Term.term_id == term_id).with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if term is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    return term


def ensure_unique(db: Session, model: type, **keys: Any) -> None:
    conditions = [getattr(model, name) == value for name, value in keys.items()]
    if db.scalar(select(func.count()).select_from(model).where(*conditions)):
        raise APIError(
            409,
            "DUPLICATE_RESOURCE",
            "A record with these values already exists.",
            {"conflict": {name: str(value) for name, value in keys.items()}},
        )


def ensure_reference_exists(
    db: Session,
    model: type,
    identity: Any,
    field: str,
    *,
    require_active_record: bool = False,
) -> None:
    """Reject a body-level foreign key that names no row.

    This is a constraint conflict rather than a missing page, so it is a 409;
    404 stays reserved for the resource the path addresses. With
    `require_active_record`, a retired catalog record is refused too, since
    new scheduling input must not be built on one.
    """
    record = db.get(model, identity)
    if record is None:
        raise APIError(
            409,
            "UNKNOWN_REFERENCE",
            "The request references a record that does not exist.",
            {"field": field, "value": str(identity)},
        )
    if require_active_record:
        require_active(record, field, identity)


def _block(program_code: str, year_level: int, section_letter: str) -> str:
    return f"{program_code}-{year_level}{section_letter}".upper()


def _dependent_labels(db: Session, label: str, count_stmt: Select) -> list[str]:
    """Human-readable names for the first few rows a counting statement matched.

    The row filter is lifted from the counting statement itself, so each guard
    states its condition once. One bounded query per blocking group.
    """
    where = count_stmt.whereclause
    if label == "schedule_records":
        rows = db.execute(
            select(
                ScheduleRecord.subject_code,
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                Term.academic_year,
                Term.term_number,
                Schedule.schedule_type,
                Schedule.finalized_at,
                ScheduleRecord.is_historical,
            )
            .select_from(ScheduleRecord)
            .join(Term, Term.term_id == ScheduleRecord.term_id)
            .outerjoin(Schedule, Schedule.schedule_id == ScheduleRecord.schedule_id)
            .outerjoin(
                BlockSectionOffering,
                BlockSectionOffering.class_id == ScheduleRecord.class_id,
            )
            .outerjoin(
                BlockSection, BlockSection.block_id == BlockSectionOffering.block_id
            )
            .where(where)
            .order_by(Term.academic_year.desc(), Term.term_number.desc(),
                      ScheduleRecord.subject_code, ScheduleRecord.record_id)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        labels = []
        for subject, prog, year, letter, ay, tn, kind, done, hist in rows:
            phase = "finalized" if done else "candidate"
            state = "historical" if hist else f"{kind} {phase}" if kind else "unplaced"
            block = [_block(prog, year, letter)] if prog is not None else []
            labels.append(" · ".join([subject, *block, f"{ay} T{tn} ({state})"]))
        return labels
    if label == "historical_snapshots":
        rows = db.execute(
            select(HistoricalSnapshot.academic_year, HistoricalSnapshot.term_number)
            .where(where)
            .order_by(HistoricalSnapshot.academic_year.desc(),
                      HistoricalSnapshot.term_number.desc(),
                      HistoricalSnapshot.snapshot_id)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"Snapshot {ay} T{tn}" for ay, tn in rows]
    if label == "block_classes":
        rows = db.execute(
            select(
                BlockSectionOffering.subject_code,
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                Term.academic_year,
                Term.term_number,
            )
            .select_from(BlockSectionOffering)
            .join(BlockSection, BlockSection.block_id == BlockSectionOffering.block_id)
            .join(Term, Term.term_id == BlockSectionOffering.term_id)
            .where(where)
            .order_by(BlockSectionOffering.subject_code, BlockSectionOffering.class_id)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [
            f"{subject} · {_block(prog, year, letter)} · {ay} T{tn}"
            for subject, prog, year, letter, ay, tn in rows
        ]
    if label == "block_sections":
        rows = db.execute(
            select(
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                Term.academic_year,
                Term.term_number,
            )
            .join(Term, Term.term_id == BlockSection.term_id)
            .where(where)
            .order_by(Term.academic_year.desc(), Term.term_number,
                      BlockSection.program_code, BlockSection.year_level,
                      BlockSection.section_letter, BlockSection.block_id)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"{_block(p, y, l)} · {ay} T{tn}" for p, y, l, ay, tn in rows]
    if label == "schedules":
        rows = db.execute(
            select(Schedule.schedule_type, Term.academic_year, Term.term_number)
            .join(Term, Term.term_id == Schedule.term_id)
            .where(where)
            .order_by(Schedule.created_at.desc(), Schedule.schedule_id)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"{kind.capitalize()} schedule · {ay} T{tn}" for kind, ay, tn in rows]
    if label == "subject_offerings":
        rows = db.execute(
            select(
                SubjectOffering.subject_code,
                SubjectOffering.group_no,
                Term.academic_year,
                Term.term_number,
            )
            .join(Term, Term.term_id == SubjectOffering.term_id)
            .where(where)
            .order_by(Term.academic_year.desc(), Term.term_number,
                      SubjectOffering.subject_code, SubjectOffering.group_no)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"{code} G{group} · {ay} T{tn}" for code, group, ay, tn in rows]
    if label == "curricula":
        rows = db.execute(
            select(Curriculum.program_code, Curriculum.effective_year)
            .where(where)
            .order_by(Curriculum.effective_year.desc())
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"{code} curriculum {year}" for code, year in rows]
    if label == "rooms":
        rows = db.scalars(
            select(Room.room_code).where(where).order_by(Room.room_code)
            .limit(DEPENDENT_LABEL_LIMIT)
        )
        return [f"Room {code}" for code in rows]
    return []


def ensure_not_referenced(
    db: Session,
    checks: Sequence[tuple[str, Select]],
    message: str = "Other records depend on this one and must be removed first.",
) -> None:
    """Refuse a delete while dependents exist, naming each blocking group.

    FR-MGT-003 requires the caller be told what blocks the deletion, not merely
    that something does, so every check runs. `dependents` carries the counts;
    `dependent_records` adds a bounded list of readable labels per group.
    """
    dependents: dict[str, int] = {}
    records: dict[str, dict[str, Any]] = {}
    for label, statement in checks:
        count = db.scalar(statement)
        if count:
            dependents[label] = count
            records[label] = {
                "total": count,
                "items": _dependent_labels(db, label, statement),
            }
    if dependents:
        raise APIError(
            409,
            "DEPENDENT_RECORDS_EXIST",
            message,
            {"dependents": dependents, "dependent_records": records},
        )


def lock_for_delete(db: Session, row: Any) -> None:
    """Lock `row` FOR UPDATE and re-read it before a dependent check.

    FOR UPDATE conflicts with the FOR KEY SHARE an FK-checked insert takes on
    its parent, so a concurrent referencing insert either committed already
    (and is counted) or waits until this delete finishes (then FK-fails
    cleanly). Lock order: `lock_term` first, then the row being deleted, so
    term-scoped writers that queue on the term never hold a child row we need.
    """
    pk = inspect(row).identity
    if db.get(
        type(row), pk, with_for_update=True, populate_existing=True
    ) is None:
        raise APIError(404, "NOT_FOUND", "The requested record was not found.")


def delete_guarded(
    db: Session,
    row: Any,
    checks: Sequence[tuple[str, Select]],
    *,
    before_delete: Any = None,
) -> None:
    """Lock `row`, refuse while dependents exist, delete and commit.

    Backstop: should a dependent still slip past the count, the FK RESTRICT
    (pgcode 23503) is turned into the same 409 rather than a 500. Other
    integrity errors propagate untouched.
    """
    lock_for_delete(db, row)
    ensure_not_referenced(db, checks)
    try:
        if before_delete is not None:
            before_delete()
        db.delete(row)
        db.commit()
    except IntegrityError as exc:
        if getattr(exc.orig, "pgcode", None) != "23503":
            raise
        db.rollback()
        ensure_not_referenced(db, checks)
        raise APIError(
            409,
            "DEPENDENT_RECORDS_EXIST",
            "Other records depend on this one and must be removed first.",
            {"dependents": {}, "dependent_records": {}},
        ) from exc


def require_active(record: Any, field: str, value: Any) -> None:
    """Refuse a write that would point new scheduling input at a retired record.

    Inactive catalog records stay so history can name them, but nothing new
    may be built on one. 409 like UNKNOWN_REFERENCE: the record exists, the
    request conflicts with its state.
    """
    if record.is_active is False:
        raise APIError(
            409,
            "INACTIVE_REFERENCE",
            "The request references a record that is marked inactive.",
            {"field": field, "value": str(value)},
        )


def count_where(model: type, *conditions) -> Select:
    """A counting statement shaped for `ensure_not_referenced`."""
    return select(func.count()).select_from(model).where(*conditions)


__all__ = [
    "count_where",
    "delete_guarded",
    "ensure_not_referenced",
    "ensure_reference_exists",
    "ensure_unique",
    "get_active_term",
    "lock_for_delete",
    "lock_term",
    "require_active",
    "require_active_term",
]
