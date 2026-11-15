"""Durable local worker jobs for CHR-31 / CHR-WORKER-001."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobType(StrEnum):
    GA = "ga"
    ARM = "arm"
    EVALUATION = "evaluation"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


JOB_TYPE_SQL_LIST = ", ".join(f"'{item.value}'" for item in JobType)
JOB_STATUS_SQL_LIST = ", ".join(f"'{item.value}'" for item in JobStatus)


class ExecutionJob(Base):
    """One durable, lease-fenced GA or ARM execution request."""

    __tablename__ = "execution_job"
    __table_args__ = (
        CheckConstraint(
            f"job_type IN ({JOB_TYPE_SQL_LIST})", name="ck_execution_job_type"
        ),
        CheckConstraint(
            f"status IN ({JOB_STATUS_SQL_LIST})", name="ck_execution_job_status"
        ),
        CheckConstraint(
            "timeout_seconds > 0", name="ck_execution_job_timeout_positive"
        ),
        CheckConstraint(
            "max_attempts > 0", name="ck_execution_job_max_attempts_positive"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_execution_job_attempt_count",
        ),
        CheckConstraint(
            "claimed_by IS NULL OR btrim(claimed_by) <> ''",
            name="ck_execution_job_claimed_by_not_blank",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_execution_job_error_code_safe",
        ),
        CheckConstraint(
            "error_message IS NULL OR "
            "(btrim(error_message) <> '' AND char_length(error_message) <= 1000)",
            name="ck_execution_job_error_message_not_blank",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_execution_job_updated_after_creation",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_execution_job_started_after_creation",
        ),
        CheckConstraint(
            "finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)",
            name="ck_execution_job_finished_after_start",
        ),
        CheckConstraint(
            "(status = 'queued' AND claimed_by IS NULL AND claim_token IS NULL "
            "AND lease_expires_at IS NULL AND finished_at IS NULL AND result IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND started_at IS NOT NULL "
            "AND finished_at IS NULL AND result IS NULL AND error_code IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'succeeded' AND claimed_by IS NULL AND claim_token IS NULL "
            "AND lease_expires_at IS NULL AND finished_at IS NOT NULL AND result IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status IN ('failed', 'timed_out') AND claimed_by IS NULL "
            "AND claim_token IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NOT NULL AND result IS NULL AND error_code IS NOT NULL "
            "AND error_message IS NOT NULL)",
            name="ck_execution_job_lifecycle",
        ),
        Index(
            "ix_execution_job_poll_queued",
            "created_at",
            "job_id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_execution_job_recover_stale",
            "lease_expires_at",
            "job_id",
            postgresql_where=text("status = 'running'"),
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'queued'")
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.user_id",
            name="fk_execution_job_requested_by",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
