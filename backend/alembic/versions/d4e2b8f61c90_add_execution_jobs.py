"""add durable execution jobs

Revision ID: d4e2b8f61c90
Revises: c3f1a7d90b45
Create Date: 2026-08-30 12:00:00.000000

CHR-31 / CHR-WORKER-001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4e2b8f61c90"
down_revision: Union[str, Sequence[str], None] = "c3f1a7d90b45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_job",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.String(length=10), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "job_type IN ('ga', 'arm')", name="ck_execution_job_type"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'timed_out')",
            name="ck_execution_job_status",
        ),
        sa.CheckConstraint(
            "timeout_seconds > 0", name="ck_execution_job_timeout_positive"
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name="ck_execution_job_max_attempts_positive"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_execution_job_attempt_count",
        ),
        sa.CheckConstraint(
            "claimed_by IS NULL OR btrim(claimed_by) <> ''",
            name="ck_execution_job_claimed_by_not_blank",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_execution_job_error_code_safe",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR "
            "(btrim(error_message) <> '' AND char_length(error_message) <= 1000)",
            name="ck_execution_job_error_message_not_blank",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name="ck_execution_job_updated_after_creation"
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_execution_job_started_after_creation",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)",
            name="ck_execution_job_finished_after_start",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["app_user.user_id"],
            name="fk_execution_job_requested_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_execution_job_poll_queued",
        "execution_job",
        ["created_at", "job_id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_execution_job_recover_stale",
        "execution_job",
        ["lease_expires_at", "job_id"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_execution_job_recover_stale", table_name="execution_job")
    op.drop_index("ix_execution_job_poll_queued", table_name="execution_job")
    op.drop_table("execution_job")
