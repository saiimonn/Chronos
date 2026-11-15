import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AuditEvent(Base):
    """One append-only record of a protected operation (NFR-SAFE-001/003/004/005).

    Database triggers reject UPDATE, DELETE and TRUNCATE. ``details`` holds only
    the redacted metadata written by ``app.services.audit.record_event``.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'rejected')", name="ck_audit_event_outcome"
        ),
        Index("ix_audit_event_occurred_at", "occurred_at"),
        Index("ix_audit_event_operation", "operation"),
        Index("ix_audit_event_target", "target_type", "target_id"),
        Index("ix_audit_event_actor", "actor_user_id"),
    )

    audit_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    # No foreign key: the trail must outlive the user row it names.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
