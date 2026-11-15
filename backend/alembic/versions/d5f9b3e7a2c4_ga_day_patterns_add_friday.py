"""Add Friday-only (F) to the GA's meeting-day patterns.

b8f6e5d7c9a1 left F out because a single Friday meeting gave a 3-unit
lecture only 90 weekly minutes. F now holds both weekly sessions back to
back (180 minutes for a lecture, 300 for a lab), matching MW and TTh. Only
subjects that prefer F are drawn onto it; the rest keep MW or TTh.

The GA configuration is versioned. If the active version carries exactly
["MW", "TTh"], this saves the same parameters with ["MW", "TTh", "F"] as the
next version and makes it the active one. A configuration an administrator
has already changed is left alone.

Revision ID: d5f9b3e7a2c4
Revises: c4e8a2d6f1b3
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "d5f9b3e7a2c4"
down_revision = "c4e8a2d6f1b3"
branch_labels = None
depends_on = None

NEW_VERSION_ID = "00000000-0000-4000-8000-000000000d5f"
OLD_PATTERNS = ["MW", "TTh"]
NEW_PATTERNS = ["MW", "TTh", "F"]


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
