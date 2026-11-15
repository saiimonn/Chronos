"""Add faculty.day_availability_masks: per-day overrides of the availability mask.

Lets a member keep their own hours on some days (for example a shorter
Monday) while every other day uses ``availability_mask``. Empty for every
existing row, so all saved availability keeps its meaning.

Revision ID: d4e6f8a0b2c3
Revises: a1c3e5f7b9d2
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e6f8a0b2c3"
down_revision = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faculty",
        sa.Column(
            "day_availability_masks", sa.Text(), nullable=False, server_default=""
        ),
    )


def downgrade() -> None:
    op.drop_column("faculty", "day_availability_masks")
