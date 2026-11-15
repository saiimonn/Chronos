"""add curriculum and curriculum requirement persistence

Revision ID: f7b3c1d5e9a2
Revises: e4f6a8b0c2d1
Create Date: 2026-09-20 00:00:00.000000

CHR-103 / CHR-CURR-002
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b3c1d5e9a2"
down_revision: Union[str, Sequence[str], None] = "e4f6a8b0c2d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "curriculum",
        sa.Column("curriculum_id", sa.UUID(), nullable=False),
        sa.Column("program_code", sa.String(length=20), nullable=False),
        sa.Column("effective_year", sa.String(length=9), nullable=False),
        # Resolution compares effective years as plain strings, which holds only
        # while the format is fixed-width.
        sa.CheckConstraint(
            "effective_year ~ '^[0-9]{4}-[0-9]{4}$'",
            name="ck_curriculum_effective_year_format",
        ),
        sa.ForeignKeyConstraint(
            ["program_code"],
            ["course_program.program_code"],
            name="fk_curriculum_program_code",
        ),
        sa.PrimaryKeyConstraint("curriculum_id"),
        sa.UniqueConstraint(
            "program_code",
            "effective_year",
            name="uq_curriculum_program_effective_year",
        ),
    )
    op.create_table(
        "curriculum_requirement",
        sa.Column("requirement_id", sa.UUID(), nullable=False),
        sa.Column("curriculum_id", sa.UUID(), nullable=False),
        sa.Column("year_level", sa.Integer(), nullable=False),
        sa.Column("term_number", sa.Integer(), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "year_level BETWEEN 1 AND 5",
            name="ck_curriculum_requirement_year_level_range",
        ),
        sa.CheckConstraint(
            "term_number BETWEEN 1 AND 3",
            name="ck_curriculum_requirement_term_number_range",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_id"],
            ["curriculum.curriculum_id"],
            name="fk_curriculum_requirement_curriculum_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            name="fk_curriculum_requirement_subject_code",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("requirement_id"),
        sa.UniqueConstraint(
            "curriculum_id",
            "year_level",
            "term_number",
            "subject_code",
            name="uq_curriculum_requirement_placement",
        ),
    )
    # No unique constraint here starts with subject_code, so without this index
    # the RESTRICT check on deleting a Subject scans the whole table.
    op.create_index(
        "ix_curriculum_requirement_subject_code",
        "curriculum_requirement",
        ["subject_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_curriculum_requirement_subject_code",
        table_name="curriculum_requirement",
    )
    op.drop_table("curriculum_requirement")
    op.drop_table("curriculum")
