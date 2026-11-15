"""subject allowed rooms

Revision ID: b1d3f5a7c9e2
Revises: a7c9e1f3b5d8
Create Date: 2026-11-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1d3f5a7c9e2"
down_revision: Union[str, Sequence[str], None] = "a7c9e1f3b5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subject_allowed_room",
        sa.Column("subject_code", sa.String(length=20), nullable=False),
        sa.Column("room_code", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["subject_code"],
            ["subject.subject_code"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_code"], ["room.room_code"], ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("subject_code", "room_code"),
    )
    op.create_index(
        "ix_subject_allowed_room_room_code", "subject_allowed_room", ["room_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_subject_allowed_room_room_code", table_name="subject_allowed_room")
    op.drop_table("subject_allowed_room")
