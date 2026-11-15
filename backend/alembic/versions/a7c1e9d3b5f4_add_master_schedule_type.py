"""add master schedule type

Revision ID: a7c1e9d3b5f4
Revises: f2a6d8e0c4b1
Create Date: 2026-09-01 09:00:00.000000

CHR-41 / CHR-SCH-002
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c1e9d3b5f4"
down_revision: Union[str, Sequence[str], None] = "f2a6d8e0c4b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schedule",
        sa.Column(
            "schedule_type",
            sa.String(length=10),
            server_default=sa.text("'routine'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_schedule_type",
        "schedule",
        "schedule_type IN ('routine', 'master')",
    )
    op.create_index(
        "uq_schedule_one_master_per_term",
        "schedule",
        ["term_id"],
        unique=True,
        postgresql_where=sa.text("schedule_type = 'master'"),
    )


def downgrade() -> None:
    op.drop_index("uq_schedule_one_master_per_term", table_name="schedule")
    op.drop_constraint("ck_schedule_type", "schedule", type_="check")
    op.drop_column("schedule", "schedule_type")
