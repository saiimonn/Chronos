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


class GAConfiguration(Base):
    """One saved version of the server-owned GA configuration (FR-CFG-001).

    Versions are never edited: saving a configuration inserts the next version
    and makes it the only active one, so a past job's settings stay traceable.
    Jobs copy the active parameters into their payload at submission, so a
    later save does not change a queued run.
    """

    __tablename__ = "ga_configuration"
    __table_args__ = (
        UniqueConstraint("version", name="uq_ga_configuration_version"),
        Index(
            "uq_ga_configuration_single_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    ga_configuration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The GAConfig fields, in the job-payload encoding (encode_ga_config).
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.user_id"), nullable=True
    )
