import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HardConstraintConfiguration(Base):
    """One saved version of the hard-constraint limits (ConstraintLimits).

    Versioned like GAConfiguration: a save inserts the next version and makes
    it the only active one. Problems copy the active limits when built, so a
    queued GA run keeps the limits it was submitted with.
    """

    __tablename__ = "hard_constraint_configuration"
    __table_args__ = (
        UniqueConstraint("version", name="uq_hard_constraint_configuration_version"),
        Index(
            "uq_hard_constraint_configuration_single_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    hard_constraint_configuration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The ConstraintLimits fields (encode_constraint_limits).
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.user_id"), nullable=True
    )
