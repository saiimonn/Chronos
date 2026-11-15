"""add immutable historical snapshots

Revision ID: c3f1a7d90b45
Revises: b7c9d1e3f5a7
Create Date: 2026-08-27 15:00:00.000000

The immutability triggers created here reject every UPDATE and DELETE on the
historical tables, Alembic's own included. A later revision that has to touch
existing rows — a backfill, a column rewrite — must wrap that work in
``ALTER TABLE <table> DISABLE TRIGGER USER`` / ``ENABLE TRIGGER USER``, or it
will fail with a check_violation partway through and leave the schema half
migrated.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f1a7d90b45"
down_revision: Union[str, Sequence[str], None] = "b7c9d1e3f5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HISTORICAL_TABLES = ("historical_snapshot", "historical_record")

# Application code, a manual psql session, and a future migration all reach the
# historical tables through the same path, so immutability is enforced by the
# database rather than by the ORM. check_violation is raised deliberately: it
# surfaces in SQLAlchemy as IntegrityError, the way every other constraint on
# these tables does, instead of as a generic raise_exception.
CREATE_REJECT_FUNCTION = """
CREATE OR REPLACE FUNCTION chronos_reject_historical_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'historical data is immutable: % on % is rejected', TG_OP, TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$;
"""

DROP_REJECT_FUNCTION = "DROP FUNCTION IF EXISTS chronos_reject_historical_mutation()"


def upgrade() -> None:
    op.create_table(
        "historical_snapshot",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("source_term_id", sa.UUID(), nullable=False),
        sa.Column("academic_year", sa.String(length=9), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(academic_year) <> ''",
            name="ck_historical_snapshot_academic_year_not_blank",
        ),
        sa.CheckConstraint(
            "semester >= 1", name="ck_historical_snapshot_semester_positive"
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "source_term_id", name="uq_historical_snapshot_source_term"
        ),
        sa.UniqueConstraint(
            "academic_year", "semester", name="uq_historical_snapshot_term_period"
        ),
    )

    op.create_table(
        "historical_record",
        sa.Column("historical_record_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=True),
        sa.Column("source_faculty_id", sa.UUID(), nullable=False),
        sa.Column("faculty_name", sa.String(length=150), nullable=False),
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.Column("subject_title", sa.String(length=150), nullable=False),
        sa.Column("room_code", sa.String(length=20), nullable=True),
        sa.Column("meeting_days", sa.String(length=10), nullable=False),
        sa.Column("time_slot", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "btrim(faculty_name) <> ''",
            name="ck_historical_record_faculty_name_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(subject_code) <> ''",
            name="ck_historical_record_subject_code_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(subject_title) <> ''",
            name="ck_historical_record_subject_title_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(meeting_days) <> ''",
            name="ck_historical_record_meeting_days_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(time_slot) <> ''",
            name="ck_historical_record_time_slot_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["historical_snapshot.snapshot_id"],
            name="fk_historical_record_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("historical_record_id"),
    )
    op.create_index(
        "uq_historical_record_source_record",
        "historical_record",
        ["source_record_id"],
        unique=True,
        postgresql_where=sa.text("source_record_id IS NOT NULL"),
    )
    op.create_index(
        "ix_historical_record_snapshot_id",
        "historical_record",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_historical_record_source_faculty_id",
        "historical_record",
        ["source_faculty_id"],
        unique=False,
    )
    op.create_index(
        "ix_historical_record_subject_code",
        "historical_record",
        ["subject_code"],
        unique=False,
    )

    op.execute(CREATE_REJECT_FUNCTION)
    for table_name in HISTORICAL_TABLES:
        # Row triggers cover UPDATE and DELETE; TRUNCATE fires only on a
        # statement trigger, and without one it would empty the table silently.
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_row "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION chronos_reject_historical_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_truncate "
            f"BEFORE TRUNCATE ON {table_name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION "
            "chronos_reject_historical_mutation()"
        )


def downgrade() -> None:
    # DROP TABLE is DDL, so the immutability triggers do not block it. No
    # existing table or row is touched: this revision only adds.
    for table_name in HISTORICAL_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_truncate "
            f"ON {table_name}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_row ON {table_name}"
        )
    op.execute(DROP_REJECT_FUNCTION)

    op.drop_index(
        "uq_historical_record_source_record", table_name="historical_record"
    )
    op.drop_index("ix_historical_record_subject_code", table_name="historical_record")
    op.drop_index(
        "ix_historical_record_source_faculty_id", table_name="historical_record"
    )
    op.drop_index("ix_historical_record_snapshot_id", table_name="historical_record")
    op.drop_table("historical_record")
    op.drop_table("historical_snapshot")
