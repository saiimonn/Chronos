"""CHR-183 / CHR-OPS-002 append-only audit trail for protected operations.

Revision ID: b3d5f7a9c1e4
Revises: a9c3e5f7b1d2
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b3d5f7a9c1e4"
down_revision = "a9c3e5f7b1d2"
branch_labels = None
depends_on = None

CREATE_REJECT_FUNCTION = """
CREATE OR REPLACE FUNCTION chronos_reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only: % is rejected', TG_OP
        USING ERRCODE = '23514';
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        # clock_timestamp, not now(): events in one transaction keep their order.
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        # No foreign key: the trail must outlive the user row it names.
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'rejected')", name="ck_audit_event_outcome"
        ),
    )
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])
    op.create_index("ix_audit_event_operation", "audit_event", ["operation"])
    op.create_index(
        "ix_audit_event_target", "audit_event", ["target_type", "target_id"]
    )
    op.create_index("ix_audit_event_actor", "audit_event", ["actor_user_id"])
    op.execute(CREATE_REJECT_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_audit_event_append_only_row "
        "BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION chronos_reject_audit_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_event_append_only_truncate "
        "BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION chronos_reject_audit_mutation()"
    )


def downgrade() -> None:
    op.drop_table("audit_event")
    op.execute("DROP FUNCTION IF EXISTS chronos_reject_audit_mutation()")
