"""Add rule_set_faculty_metric: each teacher's best candidate pattern per stream.

Diagnostic table for the Rule Sets page. A version stores only the rules that
passed its thresholds, so it could not say why a teacher has none; this table
records every teacher's closest candidate and which thresholds it met.

Revision ID: f2a4c6e8b0d1
Revises: c4e6a8b0d2f4
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2a4c6e8b0d1"
down_revision = "c4e6a8b0d2f4"
branch_labels = None
depends_on = None

PATTERN_TYPES = "'teacher_subject', 'teacher_day', 'teacher_time', 'teacher_subject_room'"


def upgrade() -> None:
    op.create_table(
        "rule_set_faculty_metric",
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pattern_type", sa.String(30), nullable=False),
        sa.Column("source_faculty_key", sa.String(64), nullable=False),
        sa.Column(
            "faculty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("faculty.faculty_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("faculty_name", sa.String(150), nullable=False),
        sa.Column("subject_code", sa.String(20), nullable=True),
        sa.Column("room_code", sa.String(20), nullable=True),
        sa.Column("meeting_days", sa.String(10), nullable=True),
        sa.Column("time_slot", sa.String(20), nullable=True),
        sa.Column("offering_count", sa.Integer(), nullable=False),
        sa.Column("support", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("lift", sa.Numeric(8, 4), nullable=False),
        sa.Column("term_recurrence", sa.Integer(), nullable=False),
        sa.Column("meets_support", sa.Boolean(), nullable=False),
        sa.Column("meets_confidence", sa.Boolean(), nullable=False),
        sa.Column("meets_lift", sa.Boolean(), nullable=False),
        sa.Column("meets_term_recurrence", sa.Boolean(), nullable=False),
        sa.Column("has_rule", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            f"pattern_type IN ({PATTERN_TYPES})",
            name="ck_rule_set_faculty_metric_pattern_type",
        ),
        sa.CheckConstraint(
            "support >= 0 AND support <= 1",
            name="ck_rule_set_faculty_metric_support_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rule_set_faculty_metric_confidence_range",
        ),
        sa.CheckConstraint(
            "lift >= 0", name="ck_rule_set_faculty_metric_lift_non_negative"
        ),
        sa.CheckConstraint(
            "term_recurrence >= 1 AND offering_count >= 1",
            name="ck_rule_set_faculty_metric_counts_positive",
        ),
        sa.UniqueConstraint(
            "rule_set_id",
            "pattern_type",
            "source_faculty_key",
            name="uq_rule_set_faculty_metric_teacher",
        ),
    )
    op.create_index(
        "ix_rule_set_faculty_metric_rule_set_id",
        "rule_set_faculty_metric",
        ["rule_set_id"],
    )
    op.create_index(
        "ix_rule_set_faculty_metric_faculty_id",
        "rule_set_faculty_metric",
        ["faculty_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rule_set_faculty_metric_faculty_id", "rule_set_faculty_metric")
    op.drop_index("ix_rule_set_faculty_metric_rule_set_id", "rule_set_faculty_metric")
    op.drop_table("rule_set_faculty_metric")
