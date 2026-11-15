"""CHR-78 CHR-SCH-004 add job_id to schedule

Revision ID: d9f3a7b1e2c4
Revises: c8a4e2f10b73
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9f3a7b1e2c4"
down_revision: Union[str, None] = "c8a4e2f10b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schedule",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_job.job_id", name="fk_schedule_job_id"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_schedule_job_id",
        "schedule",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_schedule_job_id", table_name="schedule")
    op.drop_constraint("fk_schedule_job_id", "schedule", type_="foreignkey")
    op.drop_column("schedule", "job_id")
