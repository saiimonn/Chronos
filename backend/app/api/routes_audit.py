from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_department_administrator
from app.core.errors import APIError
from app.schemas.common import ListResponse
from app.services import audit as service


class AuditEventResponse(BaseModel):
    audit_event_id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    actor_username: str | None
    operation: str
    target_type: str
    target_id: str | None
    outcome: str
    details: dict[str, Any]


def _to_utc(value: datetime | None, field: str) -> datetime | None:
    # Postgres timestamptz rejects offsets beyond +-15:59 that pydantic accepts.
    if value is None:
        return value
    if value.tzinfo is None:
        # Naive bounds are UTC, not whatever TimeZone the Postgres session has.
        return value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise APIError(
            422,
            "VALIDATION_ERROR",
            "The request contains invalid data.",
            {"errors": [{"field": f"query.{field}", "type": "datetime_out_of_range"}]},
        ) from None


router = APIRouter(
    prefix="/audit-events",
    tags=["audit-events"],
    dependencies=[Depends(require_department_administrator)],
)


@router.get("", response_model=ListResponse[AuditEventResponse])
def list_audit_events(
    db: Annotated[Session, Depends(get_db)],
    operation: Annotated[str | None, Query(max_length=64, pattern=r"^[^\x00]*$")] = None,
    target_type: Annotated[str | None, Query(max_length=40, pattern=r"^[^\x00]*$")] = None,
    target_id: Annotated[str | None, Query(max_length=64, pattern=r"^[^\x00]*$")] = None,
    actor_user_id: UUID | None = None,
    outcome: Literal["success", "rejected"] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> dict:
    return {
        "items": service.list_events(
            db,
            operation=operation,
            target_type=target_type,
            # Stored ids are lowercase UUID strings.
            target_id=target_id.lower() if target_id is not None else None,
            actor_user_id=actor_user_id,
            outcome=outcome,
            occurred_from=_to_utc(occurred_from, "occurred_from"),
            occurred_to=_to_utc(occurred_to, "occurred_to"),
            limit=limit,
            offset=offset,
        )
    }
