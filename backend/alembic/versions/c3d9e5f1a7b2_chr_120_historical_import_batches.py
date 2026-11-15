"""CHR-120 / CHR-ING-005 staged historical upload batches and rejections.

Revision ID: c3d9e5f1a7b2
Revises: b8f3d6a1e902
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c3d9e5f1a7b2"
down_revision = "b8f3d6a1e902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_import_batch",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_records", sa.LargeBinary(), nullable=False),
        sa.Column("term_manifest", sa.LargeBinary(), nullable=False),
        sa.Column("source_manifest", sa.LargeBinary(), nullable=False),
        sa.Column("faculty_crosswalk", sa.LargeBinary(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("populated_period_count", sa.Integer(), nullable=False),
        sa.Column("empty_period_count", sa.Integer(), nullable=False),
        sa.Column("unknown_subject_count", sa.Integer(), nullable=True),
        sa.Column("unknown_room_count", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("committed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('staged', 'committed')",
            name="ck_historical_import_batch_status",
        ),
        sa.CheckConstraint(
            "status <> 'committed' OR rejected_count = 0",
            name="ck_historical_import_batch_commit_clean",
        ),
        sa.UniqueConstraint("content_sha256", name="uq_historical_import_batch_content"),
    )
    op.create_table(
        "historical_import_rejection",
        sa.Column("rejection_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("historical_import_batch.batch_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("file", sa.String(40), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.UniqueConstraint(
            "batch_id", "ordinal", name="uq_historical_import_rejection_ordinal"
        ),
    )
    op.create_index(
        "ix_historical_import_rejection_batch_id",
        "historical_import_rejection",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_historical_import_rejection_batch_id",
        table_name="historical_import_rejection",
    )
    op.drop_table("historical_import_rejection")
    op.drop_table("historical_import_batch")
