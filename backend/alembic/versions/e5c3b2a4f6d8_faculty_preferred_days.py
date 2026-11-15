"""Record each faculty member's preferred teaching days.

A soft preference shown beside canonical availability. It is not a hard
constraint: the availability mask stays the only record of when someone can
teach (FR-AVL-001/002), and an empty value means "no preference".

Stored as comma-separated weekday codes in calendar order (e.g. "MON,WED,FRI").

Revision ID: e5c3b2a4f6d8
Revises: d4b2a1f3e5c7
"""

import sqlalchemy as sa
from alembic import op

revision = "e5c3b2a4f6d8"
down_revision = "d4b2a1f3e5c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faculty",
        sa.Column(
            "preferred_days",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("faculty", "preferred_days")
