from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import DCISM_CALENDAR, generate_windows
from app.models import Faculty, ScheduleRecord, TimeSlot
from app.schemas.time_slot import TimeSlotCreate, TimeSlotUpdate
from app.services.catalog.guards import (
    count_where,
    delete_guarded,
    ensure_unique,
    lock_term,
    require_active_term,
)


def list_time_slots(db: Session, term_id: UUID) -> list[TimeSlot]:
    return list(
        db.scalars(
            select(TimeSlot)
            .where(TimeSlot.term_id == term_id)
            .order_by(TimeSlot.ordinal, TimeSlot.time_slot_id)
        ).all()
    )


def ensure_canonical_slots(
    db: Session, term_id: UUID, *, require_active: bool = False,
) -> list[TimeSlot]:
    """Materialize the calendar's meeting windows for a term.

    Identifiers are derived from the term and the window, so a window already
    present is recognised without comparing times, and re-running this is a
    no-op. It flushes rather than commits: the caller owns the transaction, and
    term creation wants the term and its grid to land together or not at all.
    """

    lock_term(db, term_id)
    if require_active:
        require_active_term(db, term_id)
    existing = set(
        db.scalars(
            select(TimeSlot.time_slot_id).where(TimeSlot.term_id == term_id)
        ).all()
    )
    added = False
    for window in generate_windows(DCISM_CALENDAR):
        slot_id = window.slot_id(term_id)
        if slot_id in existing:
            continue
        db.add(
            TimeSlot(
                time_slot_id=slot_id,
                term_id=term_id,
                label=window.label,
                start_time=window.start_time,
                end_time=window.end_time,
                ordinal=window.ordinal,
            )
        )
        added = True
    if added:
        _invalidate_availability_reviews(db, term_id)
    db.flush()
    return list_time_slots(db, term_id)


def get_time_slot(db: Session, time_slot_id: UUID) -> TimeSlot:
    slot = db.get(TimeSlot, time_slot_id)
    if slot is None:
        raise APIError(404, "NOT_FOUND", "The requested time slot was not found.")
    return slot


def create_time_slot(db: Session, payload: TimeSlotCreate) -> TimeSlot:
    lock_term(db, payload.term_id)
    require_active_term(db, payload.term_id)
    ensure_unique(db, TimeSlot, term_id=payload.term_id, ordinal=payload.ordinal)
    ensure_unique(db, TimeSlot, term_id=payload.term_id, label=payload.label)
    slot = TimeSlot(**payload.model_dump())
    db.add(slot)
    _invalidate_availability_reviews(db, payload.term_id)
    db.commit()
    db.refresh(slot)
    return slot


def update_time_slot(
    db: Session, time_slot_id: UUID, payload: TimeSlotUpdate
) -> TimeSlot:
    slot = get_time_slot(db, time_slot_id)
    require_active_term(db, slot.term_id)

    values = payload.model_dump(exclude_unset=True)
    changed = [
        field for field in ("ordinal", "start_time", "end_time")
        if values.get(field) is not None and values[field] != getattr(slot, field)
    ]
    if changed:
        raise APIError(
            409,
            "TIME_SLOT_IMMUTABLE",
            "Time-slot ordering and boundaries cannot be changed because "
            "faculty availability refers to these windows.",
            {"fields": changed},
        )

    label = values.get("label")
    if label is not None and label != slot.label:
        ensure_unique(db, TimeSlot, term_id=slot.term_id, label=label)

    for field, value in values.items():
        if value is not None:
            setattr(slot, field, value)
    db.commit()
    db.refresh(slot)
    return slot


def delete_time_slot(db: Session, time_slot_id: UUID) -> None:
    """Remove a slot from the active term's grid.

    Keep masks untouched but require review again, even if another operation
    restores the previous grid length with different windows.
    """
    slot = get_time_slot(db, time_slot_id)
    lock_term(db, slot.term_id)
    require_active_term(db, slot.term_id)
    delete_guarded(
        db,
        slot,
        [
            (
                "schedule_records",
                count_where(
                    ScheduleRecord, ScheduleRecord.time_slot_id == time_slot_id
                ),
            )
        ],
        before_delete=lambda: _invalidate_availability_reviews(db, slot.term_id),
    )


def _invalidate_availability_reviews(db: Session, term_id: UUID) -> None:
    """CHR-162 / CHR-FIX-002: a changed grid invalidates its mask interpretation."""
    db.execute(
        update(Faculty)
        .where(Faculty.availability_reviewed_term_id == term_id)
        .values(availability_reviewed_term_id=None)
    )
