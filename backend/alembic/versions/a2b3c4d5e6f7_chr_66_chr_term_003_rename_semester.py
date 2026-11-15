"""CHR-66 CHR-TERM-003 rename semester to term_number

Revision ID: a2b3c4d5e6f7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── term table ───────────────────────────────────────────────────────
    # Drop the old unique constraint before renaming the column, because
    # the constraint references the old column name.
    op.drop_constraint(
        "uq_term_academic_year_semester", "term", type_="unique"
    )
    op.alter_column("term", "semester", new_column_name="term_number")
    op.create_unique_constraint(
        "uq_term_academic_year_term_number",
        "term",
        ["academic_year", "term_number"],
    )

    # ── historical_snapshot table ─────────────────────────────────────────
    op.drop_constraint(
        "uq_historical_snapshot_term_period", "historical_snapshot", type_="unique"
    )
    op.drop_constraint(
        "ck_historical_snapshot_semester_positive",
        "historical_snapshot",
        type_="check",
    )
    op.alter_column(
        "historical_snapshot", "semester", new_column_name="term_number"
    )
    op.create_unique_constraint(
        "uq_historical_snapshot_term_period",
        "historical_snapshot",
        ["academic_year", "term_number"],
    )
    op.create_check_constraint(
        "ck_historical_snapshot_term_number_positive",
        "historical_snapshot",
        "term_number >= 1",
    )


def downgrade() -> None:
    # ── historical_snapshot (reverse order) ──────────────────────────────
    op.drop_constraint(
        "ck_historical_snapshot_term_number_positive",
        "historical_snapshot",
        type_="check",
    )
    op.drop_constraint(
        "uq_historical_snapshot_term_period", "historical_snapshot", type_="unique"
    )
    op.alter_column(
        "historical_snapshot", "term_number", new_column_name="semester"
    )
    op.create_check_constraint(
        "ck_historical_snapshot_semester_positive",
        "historical_snapshot",
        "semester >= 1",
    )
    op.create_unique_constraint(
        "uq_historical_snapshot_term_period",
        "historical_snapshot",
        ["academic_year", "semester"],
    )

    # ── term ─────────────────────────────────────────────────────────────
    op.drop_constraint(
        "uq_term_academic_year_term_number", "term", type_="unique"
    )
    op.alter_column("term", "term_number", new_column_name="semester")
    op.create_unique_constraint(
        "uq_term_academic_year_semester",
        "term",
        ["academic_year", "semester"],
    )
