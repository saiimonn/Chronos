"""Persist versioned hard-constraint limits for the Hard Constraints page.

No version is seeded: with none saved, problems use the ConstraintLimits code
defaults, which are the department's standing rules.

Revision ID: a1d3f5b7c9e2
Revises: e5f7a9b1c3d4
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1d3f5b7c9e2"
down_revision = "e5f7a9b1c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hard_constraint_configuration",
        sa.Column(
            "hard_constraint_configuration_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.user_id"),
            nullable=True,
        ),
        sa.UniqueConstraint("version", name="uq_hard_constraint_configuration_version"),
    )
    op.create_index(
        "uq_hard_constraint_configuration_single_active",
        "hard_constraint_configuration",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_hard_constraint_configuration_single_active",
        table_name="hard_constraint_configuration",
    )
    op.drop_table("hard_constraint_configuration")
