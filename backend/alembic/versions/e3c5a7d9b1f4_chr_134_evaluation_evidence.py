"""CHR-134 / CHR-EVAL-011 retain checkpointed evaluation evidence.

Revision ID: e3c5a7d9b1f4
Revises: d9f1b3c5e7a2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e3c5a7d9b1f4"
down_revision = "d9f1b3c5e7a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_batch", sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("evaluation_batch", sa.Column("input_snapshot", postgresql.JSONB(), nullable=True))
    op.create_foreign_key("fk_evaluation_batch_job", "evaluation_batch", "execution_job", ["job_id"], ["job_id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_evaluation_batch_job_id", "evaluation_batch", ["job_id"])
    op.add_column("evaluation_run", sa.Column("candidate", postgresql.JSONB(), nullable=True))
    op.add_column("evaluation_run", sa.Column("validation_report", postgresql.JSONB(), nullable=True))
    op.drop_constraint("ck_evaluation_run_status", "evaluation_run", type_="check")
    op.create_check_constraint("ck_evaluation_run_status", "evaluation_run", "status IN ('pending', 'running', 'succeeded', 'failed')")


def downgrade() -> None:
    # Refuse unfinished rows rather than reinterpreting or erasing their states.
    op.drop_constraint("ck_evaluation_run_status", "evaluation_run", type_="check")
    op.create_check_constraint("ck_evaluation_run_status", "evaluation_run", "status IN ('succeeded', 'failed')")
    op.drop_column("evaluation_run", "validation_report")
    op.drop_column("evaluation_run", "candidate")
    op.drop_constraint("uq_evaluation_batch_job_id", "evaluation_batch", type_="unique")
    op.drop_constraint("fk_evaluation_batch_job", "evaluation_batch", type_="foreignkey")
    op.drop_column("evaluation_batch", "input_snapshot")
    op.drop_column("evaluation_batch", "job_id")
