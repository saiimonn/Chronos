"""add schedule assignment provenance

Revision ID: b7c9d1e3f5a7
Revises: 4d8c7a1b2e6f
Create Date: 2026-08-27 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c9d1e3f5a7"
down_revision: Union[str, Sequence[str], None] = "4d8c7a1b2e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schedule",
        sa.Column("schedule_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["term.term_id"], name="fk_schedule_term_id"
        ),
        sa.PrimaryKeyConstraint("schedule_id"),
        sa.UniqueConstraint("schedule_id", "term_id", name="uq_schedule_identity_term"),
    )
    op.create_index("ix_schedule_term_id", "schedule", ["term_id"], unique=False)

    op.create_unique_constraint(
        "uq_course_section_identity_term_subject",
        "course_section",
        ["section_id", "term_id", "subject_code"],
    )
    op.create_unique_constraint(
        "uq_time_slot_identity_term",
        "time_slot",
        ["time_slot_id", "term_id"],
    )

    op.add_column("schedule_record", sa.Column("schedule_id", sa.UUID(), nullable=True))
    op.add_column("schedule_record", sa.Column("section_id", sa.UUID(), nullable=True))
    op.add_column(
        "schedule_record", sa.Column("time_slot_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_schedule_record_schedule_term",
        "schedule_record",
        "schedule",
        ["schedule_id", "term_id"],
        ["schedule_id", "term_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_schedule_record_section_term_subject",
        "schedule_record",
        "course_section",
        ["section_id", "term_id", "subject_code"],
        ["section_id", "term_id", "subject_code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_schedule_record_time_slot_term",
        "schedule_record",
        "time_slot",
        ["time_slot_id", "term_id"],
        ["time_slot_id", "term_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_schedule_record_assignment_provenance",
        "schedule_record",
        "(schedule_id IS NULL AND section_id IS NULL AND time_slot_id IS NULL) "
        "OR (schedule_id IS NOT NULL AND section_id IS NOT NULL "
        "AND time_slot_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_schedule_record_schedule_section",
        "schedule_record",
        ["schedule_id", "section_id"],
    )
    op.create_index(
        "ix_schedule_record_section_id",
        "schedule_record",
        ["section_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_record_time_slot_id",
        "schedule_record",
        ["time_slot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_record_time_slot_id", table_name="schedule_record")
    op.drop_index("ix_schedule_record_section_id", table_name="schedule_record")
    op.drop_constraint(
        "uq_schedule_record_schedule_section",
        "schedule_record",
        type_="unique",
    )
    op.drop_constraint(
        "ck_schedule_record_assignment_provenance",
        "schedule_record",
        type_="check",
    )
    op.drop_constraint(
        "fk_schedule_record_time_slot_term", "schedule_record", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_schedule_record_section_term_subject",
        "schedule_record",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_schedule_record_schedule_term", "schedule_record", type_="foreignkey"
    )
    op.drop_column("schedule_record", "time_slot_id")
    op.drop_column("schedule_record", "section_id")
    op.drop_column("schedule_record", "schedule_id")

    op.drop_constraint("uq_time_slot_identity_term", "time_slot", type_="unique")
    op.drop_constraint(
        "uq_course_section_identity_term_subject",
        "course_section",
        type_="unique",
    )
    op.drop_index("ix_schedule_term_id", table_name="schedule")
    op.drop_table("schedule")
