"""add managed building catalog

Revision ID: b9d2e4f6a8c0
Revises: a7c1e9d3b5f4
Create Date: 2026-09-02 00:00:00.000000

CHR-62 / CHR-RM-002
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d2e4f6a8c0"
down_revision: Union[str, Sequence[str], None] = "a7c1e9d3b5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "building",
        sa.Column("building_id", sa.UUID(), nullable=False),
        sa.Column("building_name", sa.String(length=100), nullable=False),
        sa.Column("building_code", sa.String(length=20), nullable=False),
        sa.CheckConstraint("btrim(building_name) <> ''", name="ck_building_name_nonblank"),
        sa.CheckConstraint(
            "btrim(building_code) <> '' AND building_code = upper(building_code)",
            name="ck_building_code_uppercase_nonblank",
        ),
        sa.PrimaryKeyConstraint("building_id"),
        sa.UniqueConstraint("building_code"),
    )
    # Room writes resolve their building by name, so the name is unique with
    # case folded rather than exactly.
    op.create_index(
        "uq_building_name_lower",
        "building",
        [sa.text("lower(building_name)")],
        unique=True,
    )
    op.add_column("room", sa.Column("building_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_room_building_id", "room", "building", ["building_id"], ["building_id"]
    )
    op.create_index("ix_room_building_id", "room", ["building_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_room_building_id", table_name="room")
    op.drop_constraint("fk_room_building_id", "room", type_="foreignkey")
    op.drop_column("room", "building_id")
    op.drop_index("uq_building_name_lower", table_name="building")
    op.drop_table("building")
