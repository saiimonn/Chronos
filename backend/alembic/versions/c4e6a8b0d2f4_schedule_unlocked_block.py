"""Per-Block lock on master schedules.

Approving a Block locks it. A row in ``schedule_unlocked_block`` marks a
Block opened again for editing; with no row a Block reads as locked, so
every Block approved before this revision stays locked.

Revision ID: c4e6a8b0d2f4
Revises: b3d5f7a9c1e2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e6a8b0d2f4"
down_revision = "b3d5f7a9c1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_unlocked_block",
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("unlocked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedule.schedule_id"],
            name="fk_schedule_unlocked_block_schedule_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["block_id"],
            ["block_section.block_id"],
            name="fk_schedule_unlocked_block_block_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unlocked_by"],
            ["app_user.user_id"],
            name="fk_schedule_unlocked_block_unlocked_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("schedule_id", "block_id"),
    )


def downgrade() -> None:
    op.drop_table("schedule_unlocked_block")
