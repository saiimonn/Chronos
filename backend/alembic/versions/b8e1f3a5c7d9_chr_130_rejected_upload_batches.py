"""CHR-130 / CHR-ING-007 keep structurally malformed uploads as rejected batches.

Revision ID: b8e1f3a5c7d9
Revises: a4d8f2c6e0b9
"""

from alembic import op

revision = "b8e1f3a5c7d9"
down_revision = "a4d8f2c6e0b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_historical_import_batch_status", "historical_import_batch", type_="check"
    )
    op.create_check_constraint(
        "ck_historical_import_batch_status",
        "historical_import_batch",
        "status IN ('staged', 'committed', 'rejected')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM historical_import_batch WHERE status = 'rejected'")
    op.drop_constraint(
        "ck_historical_import_batch_status", "historical_import_batch", type_="check"
    )
    op.create_check_constraint(
        "ck_historical_import_batch_status",
        "historical_import_batch",
        "status IN ('staged', 'committed')",
    )
