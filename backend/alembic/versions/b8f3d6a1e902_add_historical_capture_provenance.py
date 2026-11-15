"""CHR-110 / CHR-ING-002 preserve completed-term capture provenance.

Revision ID: b8f3d6a1e902
Revises: a4e8c2f6b0d3
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8f3d6a1e902"
down_revision = "a4e8c2f6b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "historical_snapshot",
        sa.Column("source_schedule_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "historical_snapshot",
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("historical_snapshot", "approved_by_user_id")
    op.drop_column("historical_snapshot", "source_schedule_id")
