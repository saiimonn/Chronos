"""add time slots and faculty subject eligibility

Revision ID: a1b2c3d4e5f6
Revises: 7e5c3bf3dea2
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "7e5c3bf3dea2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not silently select a winner when legacy terms violate the new rules.
    # Operators must resolve these records under an approved data decision before
    # this migration can make the invariants durable.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM term
                GROUP BY academic_year, semester
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot add uq_term_academic_year_semester: resolve duplicate term identities first';
            END IF;
            IF (SELECT count(*) FROM term WHERE is_active) > 1 THEN
                RAISE EXCEPTION
                    'Cannot add uq_term_single_active: resolve multiple active terms first';
            END IF;
        END $$;
        """
    )
    op.create_unique_constraint("uq_term_academic_year_semester", "term", ["academic_year", "semester"])
    op.create_index("uq_term_single_active", "term", ["is_active"], unique=True, postgresql_where=sa.text("is_active"))
    op.create_table("time_slot", sa.Column("time_slot_id", sa.UUID(), nullable=False), sa.Column("term_id", sa.UUID(), nullable=False), sa.Column("label", sa.String(length=50), nullable=False), sa.Column("start_time", sa.Time(), nullable=False), sa.Column("end_time", sa.Time(), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.CheckConstraint("ordinal >= 0", name="ck_time_slot_ordinal_non_negative"), sa.CheckConstraint("start_time < end_time", name="ck_time_slot_time_range"), sa.ForeignKeyConstraint(["term_id"], ["term.term_id"], name="fk_time_slot_term_id"), sa.PrimaryKeyConstraint("time_slot_id"), sa.UniqueConstraint("term_id", "label", name="uq_time_slot_term_label"), sa.UniqueConstraint("term_id", "ordinal", name="uq_time_slot_term_ordinal"))
    op.create_index("ix_time_slot_term_id", "time_slot", ["term_id"])
    op.create_table("faculty_subject_eligibility", sa.Column("term_id", sa.UUID(), nullable=False), sa.Column("faculty_id", sa.UUID(), nullable=False), sa.Column("subject_code", sa.String(length=20), nullable=False), sa.ForeignKeyConstraint(["faculty_id"], ["faculty.faculty_id"], name="fk_eligibility_faculty_id"), sa.ForeignKeyConstraint(["subject_code"], ["subject.subject_code"], name="fk_eligibility_subject_code"), sa.ForeignKeyConstraint(["term_id"], ["term.term_id"], name="fk_eligibility_term_id"), sa.PrimaryKeyConstraint("term_id", "faculty_id", "subject_code"))


def downgrade() -> None:
    op.drop_table("faculty_subject_eligibility")
    op.drop_index("ix_time_slot_term_id", table_name="time_slot")
    op.drop_table("time_slot")
    op.drop_index("uq_term_single_active", table_name="term", postgresql_where=sa.text("is_active"))
    op.drop_constraint("uq_term_academic_year_semester", "term", type_="unique")
