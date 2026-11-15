"""CHR-128 / CHR-ING-006 store teacher-subject-only historical records.

FR-ING-002 requires only teacher, subject and term metadata for 2018-2022
history, and FR-ING-003 retains only those fields for that tier, so meeting
days and time slot become optional. They stay paired: a record has both or
neither.

Revision ID: f2a6c8e0b4d7
Revises: e5b7d9f1a3c6
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a6c8e0b4d7"
down_revision = "e5b7d9f1a3c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("historical_record", "meeting_days", nullable=True)
    op.alter_column("historical_record", "time_slot", nullable=True)
    op.create_check_constraint(
        "ck_historical_record_days_and_time_paired",
        "historical_record",
        "(meeting_days IS NULL) = (time_slot IS NULL)",
    )


def downgrade() -> None:
    # History is immutable, so teacher-subject-only rows cannot be backfilled
    # or deleted here; refuse rather than leave the schema half-reverted.
    has_partial_rows = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM historical_record WHERE meeting_days IS NULL)")
    )
    if has_partial_rows:
        raise RuntimeError(
            "historical_record holds teacher-subject-only rows; restore a "
            "pre-upgrade backup instead of downgrading"
        )
    op.drop_constraint(
        "ck_historical_record_days_and_time_paired", "historical_record", type_="check"
    )
    op.alter_column("historical_record", "time_slot", nullable=False)
    op.alter_column("historical_record", "meeting_days", nullable=False)
