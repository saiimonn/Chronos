from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import APIError
from app.domain import RoomType, canonical_room_code, derive_room_capacity
from app.models import Building, Room, Subject
from app.schemas.catalog import RoomCreate, RoomUpdate
from app.services.catalog.buildings import get_building_by_name
from app.services.catalog.guards import ensure_unique, require_active


def list_rooms(db: Session, include_inactive: bool = False) -> list[Room]:
    query = (
        select(Room)
        .options(selectinload(Room.allowed_subjects))
        .order_by(Room.room_code)
    )
    if not include_inactive:
        query = query.where(Room.is_active)
    return list(db.scalars(query).all())


def get_room(db: Session, room_code: str) -> Room:
    room = db.get(Room, room_code)
    if room is None:
        raise APIError(404, "NOT_FOUND", "The requested room was not found.")
    return room


def _resolve_subjects(db: Session, codes: list[str]) -> list[Subject]:
    """Load the named subjects, refusing any code the catalog does not have."""
    if not codes:
        return []
    found = list(
        db.scalars(
            select(Subject)
            .where(Subject.subject_code.in_(codes))
            .order_by(Subject.subject_code)
        ).all()
    )
    unknown = sorted(set(codes) - {subject.subject_code for subject in found})
    if unknown:
        raise APIError(
            422,
            "UNKNOWN_SUBJECT",
            "Allowed subjects must be catalog subjects.",
            {"subject_codes": unknown},
        )
    return found


def create_room(db: Session, payload: RoomCreate) -> Room:
    values = payload.model_dump()
    allowed_subjects = _resolve_subjects(db, values.pop("allowed_subject_codes"))
    building = get_building_by_name(
        db, values["building_name"], require_active_building=True
    )
    # One spelling per room ("LB486", the department's), whether the caller
    # sent "LB 486", "LB-486" or the building code twice, so no room exists
    # under two codes and imported records and rules still match it.
    values["room_code"] = canonical_room_code(
        building.building_code, values["room_code"]
    )
    if len(values["room_code"]) > 20:
        raise APIError(
            422, "VALIDATION_ERROR", "The room code must be at most 20 characters."
        )
    ensure_unique(db, Room, room_code=values["room_code"])
    # Store the catalog's spelling, not the caller's, so the denormalized name
    # stays identical to the Building it points at.
    values["building_name"] = building.building_name
    room_type = RoomType(values.pop("room_type"))
    expanded = values.pop("expanded", False)
    capacity = values.pop("capacity", None) or derive_room_capacity(
        room_type, expanded
    )
    room = Room(
        **values,
        building_id=building.building_id,
        room_type=room_type.value,
        expanded=expanded,
        capacity=capacity,
        allowed_subjects=allowed_subjects,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def update_room(db: Session, room_code: str, payload: RoomUpdate) -> Room:
    """Edit a room, including retiring (is_active=False) or restoring it.

    A room is never deleted: past schedules and mined rules name it. A retired
    room stays resolvable but is never offered to the GA or a placement edit.
    An active room must sit in an active building, so moving it into, or
    restoring it inside, a retired building is refused.
    """
    room = get_room(db, room_code)
    values = payload.model_dump(exclude_unset=True)
    subject_codes = values.pop("allowed_subject_codes", None)
    if subject_codes is not None:
        room.allowed_subjects = _resolve_subjects(db, subject_codes)
    stays_active = values.get("is_active", room.is_active) is not False
    if values.get("building_name") is not None:
        building = get_building_by_name(
            db, values["building_name"], require_active_building=stays_active
        )
        room.building_id = building.building_id
        values["building_name"] = building.building_name
    elif values.get("is_active") is True and room.building_id is not None:
        building = db.get(
            Building,
            room.building_id,
            with_for_update={"read": True},
            populate_existing=True,
        )
        if building is not None:
            require_active(building, "building_name", building.building_name)
    # An explicit capacity overrides the derived one and null resets it.
    # Without one, capacity is re-derived only when room_type or expanded
    # changes, so an override survives edits to the building or active flag.
    capacity_set = "capacity" in values
    capacity_raw = values.pop("capacity", None)
    room_type_raw = values.pop("room_type", None)
    expanded_raw = values.pop("expanded", None)
    if room_type_raw is not None:
        room.room_type = RoomType(room_type_raw).value
    if expanded_raw is not None:
        room.expanded = expanded_raw
    for field, value in values.items():
        if value is not None:
            setattr(room, field, value)
    if capacity_raw is not None:
        room.capacity = capacity_raw
    elif capacity_set or room_type_raw is not None or expanded_raw is not None:
        room.capacity = derive_room_capacity(RoomType(room.room_type), room.expanded)
    db.commit()
    db.refresh(room)
    return room


def deactivate_room(db: Session, room_code: str) -> Room:
    """Retire a room without removing the row (what DELETE does)."""
    return update_room(db, room_code, RoomUpdate(is_active=False))
