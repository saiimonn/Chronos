"""Add the subjects each faculty member prefers to teach.

``faculty_preferred_subject`` links a faculty member to the catalog subjects
they prefer. It is a soft GA input: when sampling a teacher for an offering,
the GA favors those who prefer its subject but still draws from everyone
available, so a preference never blocks an assignment. Fitness is unchanged.

Revision ID: e8b2d4f6a1c3
Revises: e7a9c1b3d5f8
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8b2d4f6a1c3"
down_revision = "e7a9c1b3d5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "faculty_preferred_subject",
        sa.Column("faculty_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["faculty_id"], ["faculty.faculty_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("faculty_id", "subject_code"),
    )
    op.create_index(
        op.f("ix_faculty_preferred_subject_subject_code"),
        "faculty_preferred_subject",
        ["subject_code"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_faculty_preferred_subject_subject_code"),
        table_name="faculty_preferred_subject",
    )
    op.drop_table("faculty_preferred_subject")
