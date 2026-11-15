"""persist matched evaluation analysis metrics

Revision ID: a4e8c2f6b0d3
Revises: f7b3c1d5e9a2
Create Date: 2026-10-03 00:00:00.000000

CHR-101 / CHR-COLAB-005
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4e8c2f6b0d3"
down_revision: Union[str, Sequence[str], None] = "f7b3c1d5e9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evaluation_run",
        sa.Column(
            "historical_score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_run",
        sa.Column(
            "generation_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("evaluation_run", "generation_metrics")
    op.drop_column("evaluation_run", "historical_score_breakdown")
