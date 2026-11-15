"""add subject type and duration contract

Revision ID: b2d4f6a8c0e1
Revises: b9d2e4f6a8c0
Create Date: 2026-09-01 22:30:00.000000

CHR-56 / CHR-SUB-002
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, Sequence[str], None] = "b9d2e4f6a8c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subject",
        sa.Column("subject_type", sa.String(length=3), nullable=True),
    )
    op.execute(
        "UPDATE subject SET subject_type = "
        "CASE WHEN requires_lab THEN 'LAB' ELSE 'LEC' END"
    )
    op.alter_column("subject", "subject_type", nullable=False)
    op.create_check_constraint(
        "ck_subject_type",
        "subject",
        "subject_type IN ('LAB', 'LEC')",
    )
    op.drop_column("subject", "requires_lab")

    # Existing slots and schedules stay immutable. The independent validator
    # reports legacy duration mismatches instead of silently rewriting them.


def downgrade() -> None:
    op.add_column(
        "subject",
        sa.Column("requires_lab", sa.Boolean(), nullable=True),
    )
    op.execute("UPDATE subject SET requires_lab = (subject_type = 'LAB')")
    op.alter_column("subject", "requires_lab", nullable=False)
    op.drop_constraint("ck_subject_type", "subject", type_="check")
    op.drop_column("subject", "subject_type")
