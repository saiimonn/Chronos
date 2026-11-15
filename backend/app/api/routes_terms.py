from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.models import AppUser, Term
from app.schemas.common import ListResponse
from app.schemas.historical_capture import (
    HistoricalCaptureRequest,
    HistoricalCaptureResponse,
    HistoricalRecordResponse,
)
from app.schemas.term import TermCreate, TermListItem, TermResponse, TermUpdate
from app.services.audit import audit_rejections
from app.services.catalog import terms as service
from app.services.catalog.guards import get_active_term
from app.services.ingestion import completed_term

router = APIRouter(
    prefix="/terms",
    tags=["terms"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[TermListItem])
def list_terms(db: DbSession, include_archived: bool = False) -> dict:
    terms = service.list_terms(db, include_archived=include_archived)
    captured = service.captured_term_ids(db, terms)
    return {
        "items": [
            {
                **TermResponse.model_validate(t).model_dump(),
                "is_captured": t.term_id in captured,
            }
            for t in terms
        ]
    }


@router.post("", response_model=TermResponse, status_code=status.HTTP_201_CREATED)
def create_term(payload: TermCreate, db: DbSession) -> Term:
    return service.create_term(db, payload)


# Declared before /{term_id} so the literal path wins the match.
@router.get("/active", response_model=TermResponse)
def read_active_term(db: DbSession) -> Term:
    return get_active_term(db)


@router.get("/{term_id}", response_model=TermResponse)
def read_term(term_id: UUID, db: DbSession) -> Term:
    return service.get_term(db, term_id)


@router.patch("/{term_id}", response_model=TermResponse)
def update_term(term_id: UUID, payload: TermUpdate, db: DbSession) -> Term:
    return service.update_term(db, term_id, payload)


# Terms are soft-archived, never deleted: rows that reference a term must not
# go missing. Archive hides it from setup lists; restore brings it back.
@router.post("/{term_id}/archive", response_model=TermResponse)
def archive_term(term_id: UUID, db: DbSession) -> Term:
    return service.archive_term(db, term_id)


@router.post("/{term_id}/restore", response_model=TermResponse)
def restore_term(term_id: UUID, db: DbSession) -> Term:
    return service.restore_term(db, term_id)


@router.post("/{term_id}/select", response_model=TermResponse)
def select_term(term_id: UUID, db: DbSession) -> Term:
    return service.select_term(db, term_id)


@router.get("/{term_id}/historical-snapshot", response_model=HistoricalCaptureResponse)
def read_historical_snapshot(
    term_id: UUID,
    db: DbSession,
    _user: Annotated[AppUser, Depends(require_department_administrator)],
) -> dict:
    return completed_term.read_historical_capture(db, term_id)


@router.get(
    "/{term_id}/historical-snapshot/records",
    response_model=ListResponse[HistoricalRecordResponse],
)
def list_historical_records(
    term_id: UUID,
    db: DbSession,
    _user: Annotated[AppUser, Depends(require_department_administrator)],
) -> dict:
    return {"items": completed_term.list_historical_records(db, term_id)}


@router.post(
    "/{term_id}/historical-snapshot",
    response_model=HistoricalCaptureResponse,
    status_code=status.HTTP_201_CREATED,
)
def capture_historical_snapshot(
    term_id: UUID,
    payload: HistoricalCaptureRequest,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="historical_capture.create",
        target_type="term",
        target_id=term_id,
        details={"schedule_id": payload.schedule_id},
    ):
        return completed_term.capture_completed_term(
            db, term_id, payload, approved_by_user_id=user.user_id
        )
