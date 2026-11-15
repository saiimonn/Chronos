import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class AuthSession(Base):
    """A revocable local session that stores only a digest of its opaque token."""

    __tablename__ = "auth_session"
    __table_args__ = (
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_auth_session_token_hash_sha256",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_session_expiry_after_creation",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_session_revocation_after_creation",
        ),
        UniqueConstraint("token_hash", name="uq_auth_session_token_hash"),
        Index("ix_auth_session_user_id", "user_id"),
        Index("ix_auth_session_expires_at", "expires_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.user_id",
            name="fk_auth_session_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["AppUser"] = relationship(back_populates="sessions")  # noqa: F821

    def is_valid(self, at: datetime | None = None) -> bool:
        """Return whether this session is unrevoked and has not expired."""
        checked_at = at or datetime.now(timezone.utc)
        return (
            self.revoked_at is None
            and self.created_at <= checked_at < self.expires_at
        )

    def revoke(self, at: datetime | None = None) -> None:
        if self.revoked_at is None:
            self.revoked_at = at or datetime.now(timezone.utc)
