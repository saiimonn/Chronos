from collections.abc import Collection, Mapping
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import TimeSlotDTO, Weekday, encode_preferred_days
from app.models import Faculty, Term, TimeSlot
from app.repositories.mappers import map_time_slot
from app.services.catalog.faculty import get_faculty
from app.services.catalog.guards import get_active_term
from app.services.validation.availability import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    decode_day_masks,
    encode_day_masks,
)

_codec = AvailabilityMaskCodec()

# The member, the active term, its slot grid, the default selection, and the
# per-day overrides (only days with their own hours).
AvailabilityView = tuple[
    Faculty, UUID, list[TimeSlotDTO], list[UUID], dict[Weekday, list[UUID]]
]


def _active_grid(
    db: Session, reviewed_term_id: UUID | None = None,
) -> tuple[UUID, list[TimeSlotDTO]]:
    if reviewed_term_id is None:
        term = get_active_term(db)
    else:
        # Hold selection stable through commit, and recheck after any lock wait.
        term = db.scalar(
            select(Term)
            .where(Term.term_id == reviewed_term_id, Term.is_active)
            .with_for_update(read=True)
        )
        if term is None:
            _refuse_stale()
    # Snapshot under the lock: commit releases it and expires ORM slot rows.
    slots = [
        map_time_slot(slot) for slot in db.scalars(
            select(TimeSlot)
            .where(TimeSlot.term_id == term.term_id)
            .order_by(TimeSlot.ordinal, TimeSlot.time_slot_id)
        ).all()
    ]
    return term.term_id, slots


def get_availability(
    db: Session, faculty_id: UUID
) -> AvailabilityView:
    faculty = get_faculty(db, faculty_id)
    term_id, slots = _active_grid(db)
    return _view(faculty, term_id, slots)


def set_availability(
    db: Session,
    faculty_id: UUID,
    reviewed_term_id: UUID,
    slot_ids: Collection[UUID],
    preferred_days: Collection[Weekday] | None = None,
    day_slot_ids: Mapping[Weekday, Collection[UUID]] | None = None,
) -> AvailabilityView:
    """Store a new availability selection, or change nothing at all.

    FR-AVL-004 requires the previously stored mask to survive a rejected
    submission, so encoding happens before the assignment and a failure exits
    without having touched the row.

    ``preferred_days`` is the soft day preference saved alongside the mask in
    the same write; ``None`` leaves the stored preference unchanged. It does
    not touch the mask.

    ``day_slot_ids`` replaces every per-day override (``{}`` clears them, so
    every day uses ``slot_ids``); ``None`` leaves the stored ones unchanged.
    """
    term_id, slots = _active_grid(db, reviewed_term_id)
    faculty = get_faculty(db, faculty_id)
    db.refresh(faculty, with_for_update=True)
    try:
        mask = _codec.encode(slot_ids, slots)
        day_masks = (
            None
            if day_slot_ids is None
            else encode_day_masks(
                {day: _codec.encode(ids, slots) for day, ids in day_slot_ids.items()}
            )
        )
    except AvailabilityMaskError as error:
        raise APIError(
            422,
            "INVALID_AVAILABILITY_MASK",
            "The submitted availability does not match the active term's time slots.",
            {"reason": str(error)},
        ) from error
    faculty.availability_mask = mask
    if day_masks is not None:
        faculty.day_availability_masks = day_masks
    if preferred_days is not None:
        faculty.preferred_days = encode_preferred_days(list(preferred_days))
    # Saving a selection is the "update" half of the new-term review.
    faculty.availability_reviewed_term_id = term_id
    db.commit()
    db.refresh(faculty)
    return _view(faculty, term_id, slots)


def confirm_availability(
    db: Session,
    faculty_id: UUID,
    reviewed_term_id: UUID,
    reviewed_slot_ids: Collection[UUID],
    reviewed_day_slot_ids: Mapping[Weekday, Collection[UUID]] | None = None,
) -> AvailabilityView:
    """Record that the stored mask was looked at for the active term.

    Optional since CHR-235: review state no longer blocks generation, so this
    only stamps ``availability_reviewed_term_id`` for the roster's display.

    The operator confirms what their dialog showed, so the request names the
    term and selection they reviewed. If either no longer matches what is
    stored, the confirmation is stale and refused rather than stamping a term
    or mask nobody looked at. The faculty row is locked so a concurrent save
    cannot slip in between the check and the stamp.

    Only a mask that still fits the active grid can be confirmed. An unset
    mask (a new member's) fits every grid as "open in every slot", so it can
    be confirmed as is. A saved mask that no longer fits has nothing
    meaningful to keep, so the operator has to save a selection instead.
    """
    # Grid writers also lock Term before Faculty; the reverse order deadlocks.
    term_id, slots = _active_grid(db, reviewed_term_id)
    faculty = get_faculty(db, faculty_id)
    db.refresh(faculty, with_for_update=True)
    try:
        weekly = _codec.weekly(
            faculty.availability_mask, faculty.day_availability_masks, slots
        )
    except AvailabilityMaskError as error:
        raise APIError(
            409,
            "AVAILABILITY_NEEDS_UPDATE",
            "The stored availability does not fit the active term's time slots; "
            "save a new selection instead of confirming it.",
            {"reason": str(error)},
        ) from error
    if set(reviewed_slot_ids) != set(weekly.default):
        _refuse_stale()
    if reviewed_day_slot_ids is not None and {
        day: set(ids) for day, ids in reviewed_day_slot_ids.items()
    } != {day: set(ids) for day, ids in weekly.overrides.items()}:
        _refuse_stale()
    faculty.availability_reviewed_term_id = term_id
    db.commit()
    db.refresh(faculty)
    return _view(faculty, term_id, slots)


def _refuse_stale() -> NoReturn:
    raise APIError(
        409,
        "AVAILABILITY_REVIEW_STALE",
        "The availability or active term changed after it was shown; "
        "reload it and review it again.",
    )


def _view(faculty: Faculty, term_id: UUID, slots: list[TimeSlotDTO]) -> AvailabilityView:
    return (
        faculty,
        term_id,
        slots,
        _decode(faculty.availability_mask, slots),
        _decode_days(faculty.day_availability_masks, slots),
    )


def _decode_days(
    stored: str | None, slots: list[TimeSlotDTO]
) -> dict[Weekday, list[UUID]]:
    """Per-day overrides as slot lists; an unreadable value reads as none."""
    try:
        masks = decode_day_masks(stored)
    except AvailabilityMaskError:
        return {}
    return {day: _decode(mask, slots) for day, mask in masks.items() if mask}


def _decode(mask: str, slots: list[TimeSlotDTO]) -> list[UUID]:
    """Decode a stored mask, treating an unusable one as an empty selection.

    A mask falls out of step with the grid whenever slots are added or removed.
    Reading has to keep working so an operator can see the grid and resubmit;
    the readiness report is what flags the mismatch.
    """
    try:
        selected = _codec.available_slot_ids(
            mask, slots
        )
    except AvailabilityMaskError:
        return []
    return [slot.time_slot_id for slot in slots if slot.time_slot_id in selected]
