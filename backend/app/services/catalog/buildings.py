from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Building, Room
from app.schemas.catalog import BuildingCreate, BuildingUpdate
from app.services.catalog.guards import (
    count_where,
    ensure_not_referenced,
    ensure_unique,
    lock_for_delete,
    require_active,
)


def list_buildings(db: Session, include_inactive: bool = False) -> list[Building]:
    query = select(Building).order_by(Building.building_code)
    if not include_inactive:
        query = query.where(Building.is_active)
    return list(db.scalars(query).all())


def get_building(db: Session, building_id: UUID) -> Building:
    building = db.get(Building, building_id)
    if building is None:
        raise APIError(404, "NOT_FOUND", "The requested building was not found.")
    return building


def get_building_by_name(
    db: Session, building_name: str, *, require_active_building: bool = False
) -> Building:
    """Resolve the building a Room write names.

    Matching folds case because a building name is prose a person types, and
    the catalog refuses two names that differ only by case, so at most one row
    can ever match.

    With `require_active_building` the row is held FOR SHARE until commit, so
    a concurrent deactivation (FOR UPDATE) either sees the new active room or
    runs first and this write is refused as INACTIVE_REFERENCE.
    """
    query = select(Building).where(
        func.lower(Building.building_name) == building_name.lower()
    )
    if require_active_building:
        query = query.with_for_update(read=True).execution_options(
            populate_existing=True
        )
    building = db.scalar(query)
    if building is None:
        raise APIError(
            409,
            "UNKNOWN_REFERENCE",
            "The request references a record that does not exist.",
            {"field": "building_name", "value": building_name},
        )
    if require_active_building:
        require_active(building, "building_name", building_name)
    return building


def _ensure_name_unused(
    db: Session, building_name: str, *, held_by: UUID | None = None
) -> None:
    """Reject a name already taken under any casing.

    `ensure_unique` compares exactly, which would let a case variant past the
    service and into the case-folded unique index as an IntegrityError.
    `held_by` excludes the building being renamed so it cannot collide with
    itself when only its casing changes.
    """
    conditions = [func.lower(Building.building_name) == building_name.lower()]
    if held_by is not None:
        conditions.append(Building.building_id != held_by)
    taken = db.scalar(select(func.count()).select_from(Building).where(*conditions))
    if taken:
        raise APIError(
            409,
            "DUPLICATE_RESOURCE",
            "A record with these values already exists.",
            {"conflict": {"building_name": building_name}},
        )


def create_building(db: Session, payload: BuildingCreate) -> Building:
    values = payload.model_dump()
    _ensure_name_unused(db, values["building_name"])
    ensure_unique(db, Building, building_code=values["building_code"])
    building = Building(**values)
    db.add(building)
    db.commit()
    db.refresh(building)
    return building


def update_building(
    db: Session, building_id: UUID, payload: BuildingUpdate
) -> Building:
    building = get_building(db, building_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("is_active") is False and building.is_active:
        _ensure_no_active_rooms(db, building)
    for field, value in values.items():
        if value is None or value == getattr(building, field):
            continue
        if field == "is_active":
            pass
        elif field == "building_name":
            _ensure_name_unused(db, value, held_by=building_id)
            _rename_dependent_rooms(db, building_id, value)
        else:
            ensure_unique(db, Building, **{field: value})
        setattr(building, field, value)
    db.commit()
    db.refresh(building)
    return building


def deactivate_building(db: Session, building_id: UUID) -> Building:
    """Retire a building without removing the row (what DELETE does)."""
    return update_building(db, building_id, BuildingUpdate(is_active=False))


def _ensure_no_active_rooms(db: Session, building: Building) -> None:
    """Refuse retiring a building that still holds rooms in use.

    A building is never deleted (its rooms' history names it). Retiring one
    while it still has active rooms would leave schedulable rooms in a
    building nobody can pick, so those rooms are retired or moved first. The
    lock makes a concurrent room write into this building wait for us.
    """
    lock_for_delete(db, building)
    ensure_not_referenced(
        db,
        [
            (
                "rooms",
                count_where(
                    Room, Room.building_id == building.building_id, Room.is_active
                ),
            )
        ],
        "This building still has active rooms. Deactivate or move them first.",
    )


def _rename_dependent_rooms(db: Session, building_id: UUID, building_name: str) -> None:
    """Carry a rename onto the Rooms that copy the name.

    `room.building_name` is denormalized and is what both the Room API and the
    scheduling domain read, so leaving it behind would publish a name the
    catalog no longer knows.
    """
    db.execute(
        update(Room)
        .where(Room.building_id == building_id)
        .values(building_name=building_name)
    )
