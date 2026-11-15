"""CHR-191 / CHR-FIX-020 let uncommitted historical uploads be retried.

Revision ID: d5f7b9c1e3a2
Revises: c4e6a8b0d2f1
"""

from alembic import op

revision = "d5f7b9c1e3a2"
down_revision = "c4e6a8b0d2f1"
branch_labels = None
depends_on = None

LIVE = "status = 'committed' OR (status = 'staged' AND rejected_count = 0)"


def upgrade() -> None:
    op.drop_constraint("uq_historical_import_batch_content", "historical_import_batch")
    op.create_index(
        "uq_historical_import_batch_live_content",
        "historical_import_batch",
        ["content_sha256"],
        unique=True,
        postgresql_where=LIVE,
    )


def downgrade() -> None:
    # Fails if retried content now has several batches; delete the extras first.
    op.drop_index("uq_historical_import_batch_live_content", "historical_import_batch")
    op.create_unique_constraint(
        "uq_historical_import_batch_content",
        "historical_import_batch",
        ["content_sha256"],
    )
