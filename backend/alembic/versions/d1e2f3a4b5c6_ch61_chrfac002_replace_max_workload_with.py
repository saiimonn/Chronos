"""CHR-61 CHR-FAC-002 replace max_workload with is_full_time

Revision ID: d1e2f3a4b5c6
Revises: 5b5c60f4f583
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "5b5c60f4f583"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── preflight: refuse values that cannot be classified at all ────────
    # Anything positive maps cleanly: ≤18 is part-time, ≥19 is full-time —
    # the same boundary the job codec uses to decode legacy v2 payloads.
    # Zero and negatives are nonsense, so refuse rather than guess.
    unclassifiable = conn.execute(
        sa.text(
            "SELECT faculty_id, max_workload FROM faculty "
            "WHERE max_workload <= 0"
        )
    ).fetchall()
    if unclassifiable:
        ids = [str(row.faculty_id) for row in unclassifiable]
        raise RuntimeError(
            "Cannot migrate: the following faculty have a non-positive "
            f"max_workload, which maps to neither part-time nor full-time: {ids}. "
            "Set them to 18 (part-time) or 24 (full-time) before running "
            "this migration."
        )

    # ── add is_full_time column (nullable temporarily) ───────────────────
    op.add_column(
        "faculty",
        sa.Column("is_full_time", sa.Boolean(), nullable=True),
    )

    # ── back-fill from max_workload ──────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE faculty SET is_full_time = (max_workload > 18) "
            "WHERE max_workload IS NOT NULL"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE faculty SET is_full_time = false WHERE max_workload IS NULL"
        )
    )

    # ── enforce NOT NULL ─────────────────────────────────────────────────
    op.alter_column(
        "faculty",
        "is_full_time",
        existing_type=sa.Boolean(),
        nullable=False,
    )

    # ── drop legacy max_workload ─────────────────────────────────────────
    op.drop_column("faculty", "max_workload")


def downgrade() -> None:
    conn = op.get_bind()

    op.add_column(
        "faculty",
        sa.Column("max_workload", sa.Integer(), nullable=True),
    )

    conn.execute(
        sa.text(
            "UPDATE faculty SET max_workload = CASE WHEN is_full_time THEN 24 ELSE 18 END"
        )
    )

    op.alter_column(
        "faculty",
        "max_workload",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_column("faculty", "is_full_time")
