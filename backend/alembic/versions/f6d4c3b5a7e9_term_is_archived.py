"""Soft-archive academic terms instead of deleting them.

An archived term is hidden from the setup lists but keeps its row, its
time-slot grid and anything that references it, so nothing goes missing.

Revision ID: f6d4c3b5a7e9
Revises: e5c3b2a4f6d8
"""

import sqlalchemy as sa
from alembic import op

revision = "f6d4c3b5a7e9"
down_revision = "e5c3b2a4f6d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "term",
        sa.Column(
            "is_archived", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("term", "is_archived")
