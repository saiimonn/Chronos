"""Retire catalog records with a flag instead of deleting them.

Subjects, rooms, buildings and course programs persist across terms and are
named by past schedules, snapshots and mined rules. Deleting one would leave
that history pointing at nothing, so a record the department no longer uses
is marked inactive instead: it stays resolvable for history and display, but
is never offered to a new term's scheduling.

Every existing row stays active.

Revision ID: d4b2a1f3e5c7
Revises: c3a1f0e2d4b6
"""

import sqlalchemy as sa
from alembic import op

revision = "d4b2a1f3e5c7"
down_revision = "c3a1f0e2d4b6"
branch_labels = None
depends_on = None

_TABLES = ("subject", "room", "building", "course_program")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "is_active")
