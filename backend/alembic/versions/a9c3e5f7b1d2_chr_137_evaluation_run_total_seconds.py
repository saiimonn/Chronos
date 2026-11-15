"""CHR-137 / CHR-EVAL-013 record full run time for matched evaluation runs.

Revision ID: a9c3e5f7b1d2
Revises: e3c5a7d9b1f4
"""

from alembic import op
import sqlalchemy as sa


revision = "a9c3e5f7b1d2"
down_revision = "e3c5a7d9b1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: runs recorded before CHR-137 measured only the engine loop.
    op.add_column("evaluation_run", sa.Column("run_total_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_run", "run_total_seconds")
