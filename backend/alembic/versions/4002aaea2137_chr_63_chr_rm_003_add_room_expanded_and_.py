"""CHR-63 CHR-RM-003 add room expanded and derive capacity

Revision ID: 4002aaea2137
Revises: a2b3c4d5e6f7
Create Date: 2026-09-04 11:24:26.866549

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4002aaea2137'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('room', sa.Column('expanded', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    # room_type used to be free text ("LEC", "Laboratory", "lab") and the old
    # call sites casefold-compared it. It is now cast to the RoomType enum on
    # every read, so anything outside {lecture, laboratory} would raise. Fold
    # the legacy spellings down before deriving capacity from them.
    op.execute(
        sa.text(
            "UPDATE room SET room_type = CASE "
            "  WHEN lower(room_type) LIKE 'lab%' THEN 'laboratory' "
            "  ELSE 'lecture' "
            "END"
        )
    )

    # Normalize existing room capacities to match derived values. Every row is
    # expanded = false at this point, so the base capacity is the derived one:
    # lecture 40, laboratory 20.
    op.execute(
        sa.text(
            "UPDATE room SET capacity = CASE "
            "  WHEN room_type = 'laboratory' THEN 20 "
            "  ELSE 40 "
            "END"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('room', 'expanded')
