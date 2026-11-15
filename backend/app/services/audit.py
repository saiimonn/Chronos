"""CHR-183 / CHR-OPS-002 audit trail for protected operations.

``record_event`` adds an event to the caller's session, so it commits or rolls
back with the operation it describes. ``audit_rejections`` records a refused
operation after the operation's own work is rolled back.

Callers pass identifiers, counts and hashes, never raw historical rows or file
content; ``_safe`` is the backstop that redacts credential-like keys and drops
binary values and long strings.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import AppUser, AuditEvent

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "cookie", "session", "credential")
_MAX_STRING = 500


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else _safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[binary omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    return text if len(text) <= _MAX_STRING else text[:_MAX_STRING] + "...[truncated]"


def record_event(
    db: Session,
    *,
    actor_id: UUID | None,
    operation: str,
    target_type: str,
    target_id: Any = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            operation=operation,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            outcome=outcome,
            details=_safe(details or {}),
        )
    )


@contextmanager
def audit_rejections(
    db: Session,
    *,
    actor_id: UUID | None,
    operation: str,
    target_type: str,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Record an APIError refusal of the wrapped operation, then re-raise it.

    Success events are written by the operation itself, inside its transaction.
    A refusal rolls that transaction back first, so nothing it left uncommitted
    survives; a rejected upload batch the operation already committed is kept.
    """
    try:
        yield
    except APIError as error:
        try:
            db.rollback()
            record_event(
                db,
                actor_id=actor_id,
                operation=operation,
                target_type=target_type,
                # A refused upload names the batch it kept; file it under that target.
                target_id=target_id
                if target_id is not None
                else error.details.get("batch_id"),
                outcome="rejected",
                details={
                    **(details or {}),
                    "error_code": error.code,
                    "status_code": error.status_code,
                    # Scalars only (a rejected upload's batch_id, conflict
                    # counts); lists such as every unassigned class would make
                    # unbounded, undeletable rows.
                    "error_details": {
                        key: value
                        for key, value in error.details.items()
                        if not isinstance(value, (dict, list, tuple, set))
                    },
                },
            )
            db.commit()
        except Exception:  # never mask the refusal the caller is reporting
            logger.exception("audit_rejection_not_recorded operation=%s", operation)
            try:
                db.rollback()
            except Exception:
                logger.exception("audit_rejection_rollback_failed")
        raise


def list_events(
    db: Session,
    *,
    operation: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    actor_user_id: UUID | None = None,
    outcome: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    statement = select(AuditEvent, AppUser.username).outerjoin(
        AppUser, AppUser.user_id == AuditEvent.actor_user_id
    )
    filters = {
        AuditEvent.operation: operation,
        AuditEvent.target_type: target_type,
        AuditEvent.target_id: target_id,
        AuditEvent.actor_user_id: actor_user_id,
        AuditEvent.outcome: outcome,
    }
    for column, value in filters.items():
        if value is not None:
            statement = statement.where(column == value)
    if occurred_from is not None:
        statement = statement.where(AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        statement = statement.where(AuditEvent.occurred_at < occurred_to)
    rows = db.execute(
        statement.order_by(
            AuditEvent.occurred_at.desc(), AuditEvent.audit_event_id
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "audit_event_id": event.audit_event_id,
            "occurred_at": event.occurred_at,
            "actor_user_id": event.actor_user_id,
            "actor_username": username,
            "operation": event.operation,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "outcome": event.outcome,
            "details": event.details,
        }
        for event, username in rows
    ]
