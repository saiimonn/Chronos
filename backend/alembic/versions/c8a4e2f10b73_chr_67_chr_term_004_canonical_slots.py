"""CHR-67 CHR-TERM-004 generate canonical slots from the calendar template

Revision ID: c8a4e2f10b73
Revises: 4002aaea2137
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.alembic_support.canonical_slots import regrid_terms

# revision identifiers, used by Alembic.
revision: str = "c8a4e2f10b73"
down_revision: Union[str, Sequence[str], None] = "4002aaea2137"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    regrid_terms(op.get_bind())


def downgrade() -> None:
    # The hand-entered grid and the availability that addressed it are not
    # recoverable. The honest reversal is to leave no grid at all, so readiness
    # blocks on NO_TIME_SLOTS rather than on a silently wrong one.
    op.get_bind().execute(sa.text("DELETE FROM time_slot"))
