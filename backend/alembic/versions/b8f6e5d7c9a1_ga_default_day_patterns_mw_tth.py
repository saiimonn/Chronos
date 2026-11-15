"""Make MW and TTh the GA's default meeting-day patterns.

Decided by the project owner on 2026-11-09. Every class meets on a two-day
pattern, so a 3-unit lecture gets 180 weekly minutes and a 3-unit lab 300,
matching T1's weekly contact time. MWF gave 270 and 450. Friday alone would
give 90 and 150, so F is not a default: patterns apply to every offering in a
run, and one class cannot have its own. See
experiments/CHR-152-CHR-EVAL-010/2026-11-01-t1-review-v3-draft/team_decision_sheet.md.

The GA configuration is versioned. If the active version still carries the
seeded ["MWF", "TTh"], this saves the same parameters with ["MW", "TTh"] as the
next version and makes it the active one. A configuration an administrator has
already changed is left alone. Frozen evaluation records keep the patterns
they were run with.

Revision ID: b8f6e5d7c9a1
Revises: f6d4c3b5a7e9
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "b8f6e5d7c9a1"
down_revision = "f6d4c3b5a7e9"
branch_labels = None
depends_on = None

NEW_VERSION_ID = "00000000-0000-4000-8000-000000000b8f"
OLD_PATTERNS = ["MWF", "TTh"]
NEW_PATTERNS = ["MW", "TTh"]


def upgrade() -> None:
    connection = op.get_bind()
    active = connection.execute(
        sa.text(
            "SELECT ga_configuration_id, parameters FROM ga_configuration "
            "WHERE is_active FOR UPDATE"
        )
    ).one_or_none()
    if active is None or active.parameters.get("meeting_day_patterns") != OLD_PATTERNS:
        return
    next_version = connection.execute(
        sa.text("SELECT COALESCE(MAX(version), 0) + 1 FROM ga_configuration")
    ).scalar_one()
    parameters = {**active.parameters, "meeting_day_patterns": NEW_PATTERNS}
    connection.execute(
        sa.text("UPDATE ga_configuration SET is_active = false WHERE is_active")
    )
    connection.execute(
        sa.text(
            "INSERT INTO ga_configuration "
            "(ga_configuration_id, version, is_active, parameters) "
            "VALUES (:id, :version, true, CAST(:parameters AS jsonb))"
        ),
        {"id": NEW_VERSION_ID, "version": next_version, "parameters": json.dumps(parameters)},
    )


def downgrade() -> None:
    connection = op.get_bind()
    created = connection.execute(
        sa.text(
            "SELECT version, is_active FROM ga_configuration "
            "WHERE ga_configuration_id = :id"
        ),
        {"id": NEW_VERSION_ID},
    ).one_or_none()
    if created is None:
        return
    connection.execute(
        sa.text("DELETE FROM ga_configuration WHERE ga_configuration_id = :id"),
        {"id": NEW_VERSION_ID},
    )
    if created.is_active:
        connection.execute(
            sa.text(
                "UPDATE ga_configuration SET is_active = true WHERE version = ("
                "SELECT MAX(version) FROM ga_configuration)"
            )
        )
