"""Mark faculty who no longer teach, so the scheduler skips them.

Past instructors stay on file so imported history can reference them, but an
inactive member is never loaded into a scheduling problem.

Revision ID: a9c1e3f5b7d2
Revises: d5f7b9c1e3a2
"""

import sqlalchemy as sa
from alembic import op

revision = "a9c1e3f5b7d2"
down_revision = "d5f7b9c1e3a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faculty",
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("faculty", "is_active")
