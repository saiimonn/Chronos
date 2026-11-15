import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ScheduleType(StrEnum):
    ROUTINE = "routine"
    MASTER = "master"


class Schedule(Base):
    """A persisted routine or master schedule for one academic term."""

    __tablename__ = "schedule"
    __table_args__ = (
        UniqueConstraint("schedule_id", "term_id", name="uq_schedule_identity_term"),
        CheckConstraint(
            "schedule_type IN ('routine', 'master')", name="ck_schedule_type"
        ),
        Index(
            "uq_schedule_one_master_per_term",
            "term_id",
            unique=True,
            postgresql_where=text("schedule_type = 'master'"),
        ),
        Index(
            "uq_schedule_job_id",
            "job_id",
            unique=True,
            postgresql_where=text("job_id IS NOT NULL"),
        ),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("term.term_id", name="fk_schedule_term_id"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_job.job_id", name="fk_schedule_job_id"),
        nullable=True,
    )
    schedule_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=ScheduleType.ROUTINE.value,
        server_default=text("'routine'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # CHR-185: the GA job result committed with the records, so a retried job
    # reports the published candidate. Only that recovery reads it.
    publication_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, deferred=True
    )

    term: Mapped["Term"] = relationship(back_populates="schedules")  # noqa: F821
    job: Mapped["ExecutionJob | None"] = relationship()  # noqa: F821
    records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="schedule",
        foreign_keys="ScheduleRecord.schedule_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Blocks approved and then opened again for editing (master schedules).
    unlocked_blocks: Mapped[list["ScheduleUnlockedBlock"]] = relationship(  # noqa: F821
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
