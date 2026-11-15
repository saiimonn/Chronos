"""CHR-115 / CHR-EVAL-009 allow matched-evaluation jobs on the local worker.

Revision ID: e5b7d9f1a3c6
Revises: a7c2e4f9b1d3
"""

from alembic import op

revision = "e5b7d9f1a3c6"
down_revision = "a7c2e4f9b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_execution_job_type", "execution_job", type_="check")
    op.create_check_constraint(
        "ck_execution_job_type",
        "execution_job",
        "job_type IN ('ga', 'arm', 'evaluation')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM execution_job WHERE job_type = 'evaluation'")
    op.drop_constraint("ck_execution_job_type", "execution_job", type_="check")
    op.create_check_constraint(
        "ck_execution_job_type", "execution_job", "job_type IN ('ga', 'arm')"
    )
