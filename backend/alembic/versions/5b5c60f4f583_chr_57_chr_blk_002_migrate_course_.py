"""CHR-57 CHR-BLK-002: migrate course sections to block sections

Revision ID: 5b5c60f4f583
Revises: c5b7e9a1d3f2
Create Date: 2026-09-03 00:54:24.332287

A course section named a cohort and a subject at once. A Block names only the
cohort, so the sections of one cohort collapse into a single row and the subject
each of them carried becomes a link to a Subject Offering.

That link is what a schedule actually assigns, so it keeps the identifier the
section had: `block_section_offering.class_id` is the old `section_id`, and
`schedule_record` reaches its class through the same value it always held. No
assignment is rewritten by this migration; only the column it lives in is
renamed.

Four things about the old rows would make the collapse wrong, and every one of
them stops the migration rather than being guessed at:

* a subject with no offering in its term, or with several - which offering a
  cohort takes was never recorded;
* more cohorts on one subject than a LAB or LEC offering may serve;
* an enrollment larger than an expanded Block seats, since capacity is no longer
  stored;
* a cohort whose sections disagree on enrollment, or whose section letters
  differ only by case.

`app.services.migration.block_preflight` reports the same set before the
migration runs and can create the missing offerings; the checks are repeated
here because the migration must be safe on its own. The limits below are
inlined rather than imported: a revision has to keep meaning what it meant when
it was written, even after the domain moves on.

Downgrading rebuilds `course_section` from the links, which keep every original
identifier. It cannot restore expected enrollment - that is gone, and the
Block's capacity stands in for it.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Mirrors of the domain rules as they stood at this revision:
# `SubjectType.max_blocks_per_offering` and the Block capacity constants.
MAX_BLOCKS_BY_SUBJECT_TYPE = "CASE s.subject_type WHEN 'LAB' THEN 1 ELSE 2 END"
BASE_CAPACITY = 20
MAX_CAPACITY = 25


# revision identifiers, used by Alembic.
revision: str = '5b5c60f4f583'
down_revision: Union[str, Sequence[str], None] = 'c5b7e9a1d3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _refuse(sql: str, message: str) -> None:
    """Abort the migration, naming every row that caused it."""
    rows = op.get_bind().execute(sa.text(sql)).all()
    if rows:
        listed = "\n".join(f"  {tuple(row)}" for row in rows)
        raise RuntimeError(f"{message}\n{listed}")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "block_section",
        sa.Column("block_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("program_code", sa.String(length=20), nullable=False),
        sa.Column("year_level", sa.Integer(), nullable=False),
        sa.Column("section_letter", sa.String(length=5), nullable=False),
        sa.Column(
            "is_expanded", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.CheckConstraint(
            "section_letter = upper(section_letter)",
            name="ck_block_section_letter_uppercase",
        ),
        sa.CheckConstraint(
            "year_level BETWEEN 1 AND 5", name="ck_block_section_year_level_range"
        ),
        sa.ForeignKeyConstraint(
            ["program_code"],
            ["course_program.program_code"],
            name="fk_block_section_program_code",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["term.term_id"], name="fk_block_section_term_id"
        ),
        sa.PrimaryKeyConstraint("block_id"),
        sa.UniqueConstraint("block_id", "term_id", name="uq_block_section_id_term"),
        sa.UniqueConstraint(
            "term_id",
            "program_code",
            "year_level",
            "section_letter",
            name="uq_block_section_cohort",
        ),
    )
    op.create_index(
        op.f("ix_block_section_program_code"), "block_section", ["program_code"]
    )
    # The association's composite foreign key references these three columns, so
    # the unique has to exist before the referencing table is created.
    op.create_unique_constraint(
        "uq_subject_offering_id_term_subject",
        "subject_offering",
        ["offering_id", "term_id", "subject_code"],
    )
    op.create_table(
        "block_section_offering",
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("block_id", sa.UUID(), nullable=False),
        sa.Column("offering_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["block_id", "term_id"],
            ["block_section.block_id", "block_section.term_id"],
            name="fk_block_section_offering_block",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offering_id", "term_id", "subject_code"],
            [
                "subject_offering.offering_id",
                "subject_offering.term_id",
                "subject_offering.subject_code",
            ],
            name="fk_block_section_offering_offering",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("class_id"),
        sa.UniqueConstraint(
            "block_id", "offering_id", name="uq_block_section_offering_pair"
        ),
        sa.UniqueConstraint(
            "block_id",
            "term_id",
            "subject_code",
            name="uq_block_section_offering_subject",
        ),
        sa.UniqueConstraint(
            "class_id",
            "term_id",
            "subject_code",
            name="uq_block_section_offering_class_term_subject",
        ),
    )
    op.create_index(
        op.f("ix_block_section_offering_block_id"),
        "block_section_offering",
        ["block_id"],
    )
    op.create_index(
        op.f("ix_block_section_offering_subject_code"),
        "block_section_offering",
        ["subject_code"],
    )

    _refuse(
        """
        SELECT cs.term_id, cs.subject_code, count(DISTINCT o.offering_id)
        FROM course_section cs
        LEFT JOIN subject_offering o
          ON o.term_id = cs.term_id AND o.subject_code = cs.subject_code
        GROUP BY cs.term_id, cs.subject_code
        HAVING count(DISTINCT o.offering_id) <> 1
        """,
        "Every course section's subject needs exactly one Subject Offering in "
        "its term before Blocks can be linked. These (term, subject, offerings) "
        "do not:",
    )
    _refuse(
        f"""
        SELECT cs.term_id, cs.subject_code, s.subject_type,
               count(DISTINCT (cs.program_code, cs.year_level,
                               upper(cs.section_letter)))
        FROM course_section cs
        JOIN subject s ON s.subject_code = cs.subject_code
        GROUP BY cs.term_id, cs.subject_code, s.subject_type
        HAVING count(DISTINCT (cs.program_code, cs.year_level,
                               upper(cs.section_letter)))
               > {MAX_BLOCKS_BY_SUBJECT_TYPE}
        """,
        "These subjects are taken by more cohorts than one offering may serve; "
        "split them across further groups first (term, subject, type, cohorts):",
    )
    _refuse(
        f"""
        SELECT term_id, program_code, year_level, upper(section_letter),
               max(expected_enrollment), count(DISTINCT expected_enrollment),
               count(DISTINCT section_letter)
        FROM course_section
        GROUP BY term_id, program_code, year_level, upper(section_letter)
        HAVING max(expected_enrollment) > {MAX_CAPACITY}
            OR count(DISTINCT expected_enrollment) > 1
            OR count(DISTINCT section_letter) > 1
        """,
        "These cohorts cannot become a single Block - the enrollment exceeds an "
        "expanded Block, the sections disagree on it, or the section letters "
        "differ only by case (term, program, year, letter, largest, distinct "
        "enrollments, distinct letters):",
    )

    # One Block per cohort, expanded when the cohort was larger than a plain
    # Block seats.
    op.execute(
        f"""
        INSERT INTO block_section
            (block_id, term_id, program_code, year_level, section_letter,
             is_expanded)
        SELECT gen_random_uuid(), term_id, program_code, year_level,
               upper(section_letter),
               max(expected_enrollment) > {BASE_CAPACITY}
        FROM course_section
        GROUP BY term_id, program_code, year_level, upper(section_letter)
        """
    )
    # Each section becomes the link between its Block and its subject's offering,
    # keeping its own identifier so nothing that referenced it has to change.
    # Exactly one offering matches each row; the first check above is what makes
    # that true.
    op.execute(
        """
        INSERT INTO block_section_offering
            (class_id, block_id, offering_id, term_id, subject_code)
        SELECT cs.section_id, b.block_id, o.offering_id, cs.term_id,
               cs.subject_code
        FROM course_section cs
        JOIN block_section b
          ON b.term_id = cs.term_id
         AND b.program_code = cs.program_code
         AND b.year_level = cs.year_level
         AND b.section_letter = upper(cs.section_letter)
        JOIN subject_offering o
          ON o.term_id = cs.term_id AND o.subject_code = cs.subject_code
        """
    )

    # Assignments already hold the right value; only the name and the table it
    # points at change.
    op.drop_constraint(
        "fk_schedule_record_section_term_subject",
        "schedule_record",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_schedule_record_assignment_provenance", "schedule_record", type_="check"
    )
    op.drop_constraint(
        "uq_schedule_record_schedule_section", "schedule_record", type_="unique"
    )
    op.alter_column("schedule_record", "section_id", new_column_name="class_id")
    op.execute(
        "ALTER INDEX ix_schedule_record_section_id "
        "RENAME TO ix_schedule_record_class_id"
    )
    op.create_check_constraint(
        "ck_schedule_record_assignment_provenance",
        "schedule_record",
        "(schedule_id IS NULL AND class_id IS NULL AND time_slot_id IS NULL) "
        "OR (schedule_id IS NOT NULL AND class_id IS NOT NULL "
        "AND time_slot_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_schedule_record_schedule_class",
        "schedule_record",
        ["schedule_id", "class_id"],
    )
    op.create_foreign_key(
        "fk_schedule_record_class_term_subject",
        "schedule_record",
        "block_section_offering",
        ["class_id", "term_id", "subject_code"],
        ["class_id", "term_id", "subject_code"],
        ondelete="RESTRICT",
    )

    op.drop_table("course_section")


def downgrade() -> None:
    """Downgrade schema."""
    # Every link kept the identifier its section had, so the sections come back
    # as they were apart from the enrollment the cutover dropped.
    op.create_table(
        "course_section",
        sa.Column("section_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.Column("program_code", sa.String(length=20), nullable=False),
        sa.Column("year_level", sa.Integer(), nullable=False),
        sa.Column("section_letter", sa.String(length=5), nullable=False),
        sa.Column("expected_enrollment", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "expected_enrollment > 0",
            name="ck_course_section_expected_enrollment_positive",
        ),
        sa.CheckConstraint(
            "year_level BETWEEN 1 AND 5", name="ck_course_section_year_level_range"
        ),
        sa.ForeignKeyConstraint(
            ["program_code"],
            ["course_program.program_code"],
            name="fk_course_section_program_code",
        ),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            name="fk_course_section_subject_code",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["term.term_id"], name="fk_course_section_term_id"
        ),
        sa.PrimaryKeyConstraint("section_id"),
        sa.UniqueConstraint(
            "section_id",
            "term_id",
            "subject_code",
            name="uq_course_section_identity_term_subject",
        ),
        sa.UniqueConstraint(
            "term_id",
            "subject_code",
            "program_code",
            "year_level",
            "section_letter",
            name="uq_course_section_offering",
        ),
    )
    op.create_index(
        "ix_course_section_cohort",
        "course_section",
        ["term_id", "program_code", "year_level", "section_letter"],
    )
    op.create_index(
        op.f("ix_course_section_program_code"), "course_section", ["program_code"]
    )
    op.create_index(
        op.f("ix_course_section_subject_code"), "course_section", ["subject_code"]
    )
    op.execute(
        f"""
        INSERT INTO course_section
            (section_id, term_id, subject_code, program_code, year_level,
             section_letter, expected_enrollment)
        SELECT l.class_id, b.term_id, l.subject_code, b.program_code,
               b.year_level, b.section_letter,
               CASE WHEN b.is_expanded THEN {MAX_CAPACITY} ELSE {BASE_CAPACITY} END
        FROM block_section_offering l
        JOIN block_section b ON b.block_id = l.block_id
        """
    )

    op.drop_constraint(
        "fk_schedule_record_class_term_subject", "schedule_record", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_schedule_record_schedule_class", "schedule_record", type_="unique"
    )
    op.drop_constraint(
        "ck_schedule_record_assignment_provenance", "schedule_record", type_="check"
    )
    op.alter_column("schedule_record", "class_id", new_column_name="section_id")
    op.execute(
        "ALTER INDEX ix_schedule_record_class_id "
        "RENAME TO ix_schedule_record_section_id"
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
    op.create_foreign_key(
        "fk_schedule_record_section_term_subject",
        "schedule_record",
        "course_section",
        ["section_id", "term_id", "subject_code"],
        ["section_id", "term_id", "subject_code"],
        ondelete="RESTRICT",
    )

    op.drop_index(
        op.f("ix_block_section_offering_subject_code"),
        table_name="block_section_offering",
    )
    op.drop_index(
        op.f("ix_block_section_offering_block_id"),
        table_name="block_section_offering",
    )
    op.drop_table("block_section_offering")
    # Dropped only once the association that references it is gone.
    op.drop_constraint(
        "uq_subject_offering_id_term_subject", "subject_offering", type_="unique"
    )
    op.drop_index(op.f("ix_block_section_program_code"), table_name="block_section")
    op.drop_table("block_section")
