"""CHR-129 / CHR-ARM-008 record mining provenance and summaries on rule versions.

A rule set now keeps the execution job and administrator that produced it, the
immutable historical snapshots it mined, and a summary of what the run counted,
excluded and persisted. Operational term rows can be renamed or removed, so the
snapshots, not ``rule_set_term``, are the lasting record of the source periods.

Revision ID: a4d8f2c6e0b9
Revises: f2a6c8e0b4d7
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4d8f2c6e0b9"
down_revision = "f2a6c8e0b4d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Provenance only, deliberately not foreign keys: removing a job or user
    # row must not rewrite which run and administrator produced a version.
    op.add_column(
        "rule_set", sa.Column("source_job_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "rule_set", sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "rule_set", sa.Column("mining_summary", postgresql.JSONB(), nullable=True)
    )
    op.create_table(
        "rule_set_source_snapshot",
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Snapshots are append-only and never deleted, so this reference and
        # the period it carries cannot change under the rule set.
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("historical_snapshot.snapshot_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_rule_set_source_snapshot_snapshot_id",
        "rule_set_source_snapshot",
        ["snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_set_source_snapshot_snapshot_id", table_name="rule_set_source_snapshot"
    )
    op.drop_table("rule_set_source_snapshot")
    op.drop_column("rule_set", "mining_summary")
    op.drop_column("rule_set", "requested_by_user_id")
    op.drop_column("rule_set", "source_job_id")
