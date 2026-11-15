"""Re-derive capacity for the LB rooms whose type c3a1f0e2d4b6 corrected.

Capacity follows room type and expansion: a laboratory seats 20 (25
expanded), a lecture room 40 (45 expanded). c3a1f0e2d4b6 changed the type
but kept the old capacity, so a corrected lab still read 40 seats and a
corrected lecture room 20. This sets every affected row back to the derived
value. Constants are inlined so the migration keeps doing what it did the
day it ran (CHR-FE-033 / CHR-240).

Downgrade is a no-op: the previous capacities were wrong.

Revision ID: a1d3c5e7f9b2
Revises: b8f6e5d7c9a1
"""

import sqlalchemy as sa
from alembic import op

revision = "a1d3c5e7f9b2"
down_revision = "b8f6e5d7c9a1"
branch_labels = None
depends_on = None

CORRECTED_ROOMS = [
    f"LB{n}" for n in (*range(443, 449), *range(467, 470), *range(483, 487))
]


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE room SET capacity = "
            "CASE room_type WHEN 'laboratory' THEN 20 ELSE 40 END "
            "+ CASE WHEN expanded THEN 5 ELSE 0 END "
            "WHERE room_code IN :codes"
        ).bindparams(sa.bindparam("codes", value=CORRECTED_ROOMS, expanding=True))
    )


def downgrade() -> None:
    pass
