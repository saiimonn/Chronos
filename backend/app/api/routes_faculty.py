from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.domain import decode_preferred_days
from app.models import Faculty
from app.schemas.availability import (
    AvailabilityConfirm,
    AvailabilityResponse,
    AvailabilityUpdate,
)
from app.schemas.common import ListResponse
from app.schemas.faculty import (
    FacultyCreate,
    FacultyResponse,
    FacultyStatusUpdate,
    FacultyUpdate,
)
from app.services.catalog import availability as availability_service
from app.services.catalog import faculty as service

router = APIRouter(
    prefix="/faculty",
    tags=["faculty"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[FacultyResponse])
def list_faculty(db: DbSession, include_inactive: bool = False) -> dict:
    # Inactive faculty are hidden unless asked for, so every schedule picker
    # offers only people who currently teach. The roster asks for all.
    return {"items": service.list_faculty(db, include_inactive)}


@router.post("", response_model=FacultyResponse, status_code=status.HTTP_201_CREATED)
def create_faculty(payload: FacultyCreate, db: DbSession) -> Faculty:
    return service.create_faculty(db, payload)


@router.post("/status", response_model=ListResponse[FacultyResponse])
def set_faculty_status(payload: FacultyStatusUpdate, db: DbSession) -> dict:
    # Bulk activate or deactivate from the roster; all or nothing.
    return {
        "items": service.set_faculty_status(db, payload.faculty_ids, payload.is_active)
    }


@router.get("/{faculty_id}", response_model=FacultyResponse)
def read_faculty(faculty_id: UUID, db: DbSession) -> Faculty:
    return service.get_faculty(db, faculty_id)


@router.patch("/{faculty_id}", response_model=FacultyResponse)
def update_faculty(faculty_id: UUID, payload: FacultyUpdate, db: DbSession) -> Faculty:
    return service.update_faculty(db, faculty_id, payload)


@router.delete("/{faculty_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faculty(faculty_id: UUID, db: DbSession) -> None:
    # Kept for API compatibility, but soft: faculty rows are never removed,
    # because schedules, history and rules reference them. DELETE marks the
    # member inactive; PATCH {"is_active": true} brings them back.
    service.deactivate_faculty(db, faculty_id)


@router.get("/{faculty_id}/availability", response_model=AvailabilityResponse)
def read_availability(faculty_id: UUID, db: DbSession) -> dict:
    return _availability(*availability_service.get_availability(db, faculty_id))


@router.put("/{faculty_id}/availability", response_model=AvailabilityResponse)
def replace_availability(
    faculty_id: UUID, payload: AvailabilityUpdate, db: DbSession
) -> dict:
    return _availability(
        *availability_service.set_availability(
            db, faculty_id, payload.term_id, payload.time_slot_ids,
            payload.preferred_days, payload.day_time_slot_ids,
        )
    )


@router.post("/{faculty_id}/availability/confirm", response_model=AvailabilityResponse)
def confirm_availability(
    faculty_id: UUID, payload: AvailabilityConfirm, db: DbSession
) -> dict:
    return _availability(
        *availability_service.confirm_availability(
            db, faculty_id, payload.term_id, payload.time_slot_ids,
            payload.day_time_slot_ids,
        )
    )


def _availability(
    faculty: Faculty,
    term_id: UUID,
    slots: list,
    selected: list[UUID],
    day_selected: dict,
) -> dict:
    return {
        "day_time_slot_ids": day_selected,
        "faculty_id": faculty.faculty_id,
        "term_id": term_id,
        "slot_grid": slots,
        "time_slot_ids": selected,
        "reviewed": faculty.availability_reviewed_term_id == term_id,
        "preferred_days": list(decode_preferred_days(faculty.preferred_days)),
    }
