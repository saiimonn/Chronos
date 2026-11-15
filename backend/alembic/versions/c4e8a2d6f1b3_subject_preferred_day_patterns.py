"""Add a soft day-pattern preference to subjects.

Some subjects are usually taught on a particular pattern, most often
Friday-only. ``preferred_day_patterns`` holds any of MW, TTh and F,
comma-separated ("TTh,F"); empty means no preference. The GA draws a
preferred pattern first and falls back to the term's others when none fits.

Revision ID: c4e8a2d6f1b3
Revises: a1d3c5e7f9b2
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2d6f1b3"
down_revision = "a1d3c5e7f9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subject",
        sa.Column(
            "preferred_day_patterns",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("subject", "preferred_day_patterns")
