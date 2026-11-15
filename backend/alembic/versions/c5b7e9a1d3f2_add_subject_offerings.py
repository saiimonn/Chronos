"""add subject offerings with sequential group numbers

Revision ID: c5b7e9a1d3f2
Revises: b2d4f6a8c0e1
Create Date: 2026-09-03 00:00:00.000000

CHR-53 / CHR-SUB-003
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5b7e9a1d3f2"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subject_offering",
        sa.Column("offering_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.Column("group_no", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "group_no > 0", name="ck_subject_offering_group_no_positive"
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["term.term_id"], name="fk_subject_offering_term_id"
        ),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            name="fk_subject_offering_subject_code",
        ),
        sa.PrimaryKeyConstraint("offering_id"),
        # The database, not the application, is what makes two concurrent
        # allocations of the same number impossible.
        sa.UniqueConstraint(
            "term_id",
            "subject_code",
            "group_no",
            name="uq_subject_offering_term_subject_group_no",
        ),
    )
    op.create_index(
        "ix_subject_offering_subject_code",
        "subject_offering",
        ["subject_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_subject_offering_subject_code", table_name="subject_offering")
    op.drop_table("subject_offering")
