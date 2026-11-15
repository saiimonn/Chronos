"""Historical uploads take the Offered Courses workbook itself.

The workbook bytes are kept for audit beside the canonical bundle derived from
it. Rows that are not history to import are recorded as non-blocking skipped
rows, so `rejected_count` keeps counting only what prevents confirmation.

Revision ID: a7c9e1f3b5d8
Revises: e8b2d4f6a1c3
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c9e1f3b5d8"
down_revision = "e8b2d4f6a1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "historical_import_batch",
        sa.Column("source_workbook", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "historical_import_batch",
        sa.Column(
            "skipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "historical_import_rejection",
        sa.Column(
            "blocking", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("historical_import_rejection", "blocking")
    op.drop_column("historical_import_batch", "skipped_count")
    op.drop_column("historical_import_batch", "source_workbook")
