import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class EvaluationBatch(Base):
    """The shared input and GA configuration for ten matched run pairs."""

    __tablename__ = "evaluation_batch"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_evaluation_batch_job_id"),
    )

    evaluation_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("term.term_id"), nullable=False, index=True
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Legacy records have no captured snapshot; do not manufacture evidence.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "execution_job.job_id", name="fk_evaluation_batch_job", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    runs: Mapped[list["EvaluationRun"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )
    job: Mapped["ExecutionJob | None"] = relationship()  # noqa: F821


class EvaluationRun(Base):
    """One guided or unguided execution retained even when it fails."""

    __tablename__ = "evaluation_run"
    __table_args__ = (
        CheckConstraint("mode IN ('guided', 'unguided')", name="ck_evaluation_run_mode"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evaluation_run_status",
        ),
        CheckConstraint("pair_index >= 0 AND pair_index < 10", name="ck_evaluation_run_pair_index"),
        UniqueConstraint(
            "evaluation_batch_id", "pair_index", "mode", name="uq_evaluation_run_pair_mode"
        ),
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    evaluation_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_batch.evaluation_batch_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pair_index: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    random_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    termination_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(nullable=True)
    # CHR-137 / CHR-EVAL-013 full run time; null for runs recorded before it.
    run_total_seconds: Mapped[float | None] = mapped_column(nullable=True)
    # Named for course sections because that is what the applied revision
    # created; the value is a Block class count since CHR-BLK-002.
    required_section_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    assigned_section_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    hard_conflict_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    historical_pattern_attainment: Mapped[float | None] = mapped_column(nullable=True)
    historical_score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generation_metrics: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    candidate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    batch: Mapped[EvaluationBatch] = relationship(back_populates="runs")
