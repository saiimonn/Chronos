"""add matched evaluation records

Revision ID: f2a6d8e0c4b1
Revises: d4e6f8a0b2c4
Create Date: 2026-08-31 01:50:00.000000

CHR-38 / CHR-EVAL-001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2a6d8e0c4b1"
down_revision: Union[str, Sequence[str], None] = "d4e6f8a0b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evaluation_batch",
        sa.Column("evaluation_batch_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["term_id"], ["term.term_id"]),
        sa.PrimaryKeyConstraint("evaluation_batch_id"),
    )
    op.create_index("ix_evaluation_batch_term_id", "evaluation_batch", ["term_id"], unique=False)
    op.create_table(
        "evaluation_run",
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_batch_id", sa.UUID(), nullable=False),
        sa.Column("pair_index", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("termination_reason", sa.String(length=30), nullable=True),
        sa.Column("termination_generation", sa.Integer(), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("required_section_count", sa.Integer(), nullable=True),
        sa.Column("assigned_section_count", sa.Integer(), nullable=True),
        sa.Column("hard_conflict_count", sa.Integer(), nullable=True),
        sa.Column("historical_pattern_attainment", sa.Float(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint("mode IN ('guided', 'unguided')", name="ck_evaluation_run_mode"),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_evaluation_run_status"),
        sa.CheckConstraint("pair_index >= 0 AND pair_index < 10", name="ck_evaluation_run_pair_index"),
        sa.ForeignKeyConstraint(["evaluation_batch_id"], ["evaluation_batch.evaluation_batch_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evaluation_run_id"),
        sa.UniqueConstraint("evaluation_batch_id", "pair_index", "mode", name="uq_evaluation_run_pair_mode"),
    )
    op.create_index("ix_evaluation_run_evaluation_batch_id", "evaluation_run", ["evaluation_batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_evaluation_run_evaluation_batch_id", table_name="evaluation_run")
    op.drop_table("evaluation_run")
    op.drop_index("ix_evaluation_batch_term_id", table_name="evaluation_batch")
    op.drop_table("evaluation_batch")
