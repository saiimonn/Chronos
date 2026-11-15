"""CHR-119 / CHR-CFG-001 persist the versioned GA configuration.

Revision ID: a7c2e4f9b1d3
Revises: c3d9e5f1a7b2
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a7c2e4f9b1d3"
down_revision = "c3d9e5f1a7b2"
branch_labels = None
depends_on = None

# The GAConfig code defaults at the time of this migration, seeded as version 1
# so exactly one configuration is active from the start. Copied rather than
# imported so later changes to the defaults cannot rewrite this migration.
_INITIAL_PARAMETERS = {
    "population_size": 50,
    "max_generations": 100,
    "crossover_rate": 0.8,
    "mutation_rate": 0.2,
    "gene_mutation_rate": 0.1,
    "tournament_size": 3,
    "elite_count": 1,
    "stagnation_limit": 20,
    "random_seed": 0,
    "meeting_day_patterns": ["MWF", "TTh"],
}


def upgrade() -> None:
    table = op.create_table(
        "ga_configuration",
        sa.Column("ga_configuration_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
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
        sa.UniqueConstraint("version", name="uq_ga_configuration_version"),
    )
    op.create_index(
        "uq_ga_configuration_single_active",
        "ga_configuration",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.bulk_insert(
        table,
        [
            {
                "ga_configuration_id": "00000000-0000-4000-8000-000000000119",
                "version": 1,
                "is_active": True,
                "parameters": _INITIAL_PARAMETERS,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("uq_ga_configuration_single_active", table_name="ga_configuration")
    op.drop_table("ga_configuration")
