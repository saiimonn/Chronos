from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.models import SubjectOffering
from app.schemas.common import ListResponse
from app.schemas.offering import OfferingCreate, OfferingResponse
from app.services.catalog import offerings as service

router = APIRouter(
    prefix="/subject-offerings",
    tags=["subject offerings"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[OfferingResponse])
def list_offerings(db: DbSession, term_id: Annotated[UUID, Query()]) -> dict:
    return {"items": service.list_offerings(db, term_id)}


@router.post("", response_model=OfferingResponse, status_code=status.HTTP_201_CREATED)
def create_offering(payload: OfferingCreate, db: DbSession) -> SubjectOffering:
    return service.create_offering(db, payload)


@router.get("/{offering_id}", response_model=OfferingResponse)
def read_offering(offering_id: UUID, db: DbSession) -> SubjectOffering:
    return service.get_offering(db, offering_id)


# No PATCH: term, subject, and Group No. are the offering's identity, so a
# change is a delete and a new offering rather than an edit in place.
@router.delete("/{offering_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_offering(offering_id: UUID, db: DbSession) -> None:
    service.delete_offering(db, offering_id)
