"""CHR-69 CHR-TERM-006 remove teacher eligibility table

Revision ID: e4f6a8b0c2d1
Revises: d9f3a7b1e2c4
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f6a8b0c2d1"
down_revision: Union[str, None] = "d9f3a7b1e2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("faculty_subject_eligibility")


def downgrade() -> None:
    op.create_table(
        "faculty_subject_eligibility",
        sa.Column("term_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("faculty_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["faculty_id"],
            ["faculty.faculty_id"],
            name="fk_eligibility_faculty_id",
        ),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            name="fk_eligibility_subject_code",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["term.term_id"],
            name="fk_eligibility_term_id",
        ),
        sa.PrimaryKeyConstraint("term_id", "faculty_id", "subject_code"),
    )
