"""add schedule finalization

Revision ID: d4e6f8a0b2c4
Revises: d4e2b8f61c90
Create Date: 2026-08-30 16:00:00.000000

CHR-36 / CHR-SCH-001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c4"
down_revision: Union[str, Sequence[str], None] = "d4e2b8f61c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schedule",
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("schedule", "finalized_at")
