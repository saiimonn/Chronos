from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.models import TimeSlot
from app.schemas.common import ListResponse
from app.schemas.time_slot import TimeSlotCreate, TimeSlotResponse, TimeSlotUpdate
from app.services.catalog import time_slots as service

router = APIRouter(
    prefix="/time-slots",
    tags=["time slots"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[TimeSlotResponse])
def list_time_slots(db: DbSession, term_id: Annotated[UUID, Query()]) -> dict:
    return {"items": service.list_time_slots(db, term_id)}


@router.post("/generate", response_model=ListResponse[TimeSlotResponse])
def generate_canonical_slots(
    db: DbSession, term_id: Annotated[UUID, Query()]
) -> dict:
    slots = service.ensure_canonical_slots(db, term_id, require_active=True)
    db.commit()
    return {"items": slots}


@router.post("", response_model=TimeSlotResponse, status_code=status.HTTP_201_CREATED)
def create_time_slot(payload: TimeSlotCreate, db: DbSession) -> TimeSlot:
    return service.create_time_slot(db, payload)


@router.get("/{time_slot_id}", response_model=TimeSlotResponse)
def read_time_slot(time_slot_id: UUID, db: DbSession) -> TimeSlot:
    return service.get_time_slot(db, time_slot_id)


@router.patch("/{time_slot_id}", response_model=TimeSlotResponse)
def update_time_slot(
    time_slot_id: UUID, payload: TimeSlotUpdate, db: DbSession
) -> TimeSlot:
    return service.update_time_slot(db, time_slot_id, payload)


@router.delete("/{time_slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_time_slot(time_slot_id: UUID, db: DbSession) -> None:
    service.delete_time_slot(db, time_slot_id)
