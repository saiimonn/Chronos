"""CHR-126 / CHR-ARM-006 link rule sets to execution jobs.

Revision ID: f6c8e0a2b4d7
Revises: e5b7d9f1a3c6
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f6c8e0a2b4d7"
down_revision = "e5b7d9f1a3c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_set",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_job.job_id", name="fk_rule_set_job_id"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_rule_set_job_id",
        "rule_set",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )
    op.add_column(
        "rule_set", sa.Column("publication_result", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("rule_set", "publication_result")
    op.drop_index("uq_rule_set_job_id", table_name="rule_set")
    op.drop_constraint("fk_rule_set_job_id", "rule_set", type_="foreignkey")
    op.drop_column("rule_set", "job_id")
