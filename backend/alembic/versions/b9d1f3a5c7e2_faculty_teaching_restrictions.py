"""Add hard teaching restrictions to faculty.

``faculty_non_preferred_subject`` lists catalog subjects a faculty member must
never be assigned. ``faculty.lecture_only`` bars them from laboratory
subjects. Both are hard constraints: the GA never draws such a teacher for
the subject, and the validator flags a placement that does.

Revision ID: b9d1f3a5c7e2
Revises: b1d3f5a7c9e2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b9d1f3a5c7e2"
down_revision = "b1d3f5a7c9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faculty",
        sa.Column(
            "lecture_only", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "faculty_non_preferred_subject",
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
        op.f("ix_faculty_non_preferred_subject_subject_code"),
        "faculty_non_preferred_subject",
        ["subject_code"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_faculty_non_preferred_subject_subject_code"),
        table_name="faculty_non_preferred_subject",
    )
    op.drop_table("faculty_non_preferred_subject")
    op.drop_column("faculty", "lecture_only")
