"""CHR-185 / CHR-FIX-015 store the GA job result with its schedule.

Revision ID: c4e6a8b0d2f1
Revises: b3d5f7a9c1e4
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c4e6a8b0d2f1"
down_revision = "b3d5f7a9c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: schedules published before CHR-185 have no stored result.
    op.add_column(
        "schedule", sa.Column("publication_result", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("schedule", "publication_result")
