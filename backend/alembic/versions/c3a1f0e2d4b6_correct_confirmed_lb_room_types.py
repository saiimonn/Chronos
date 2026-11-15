"""Correct the room types the department confirmed for the LB rooms.

Confirmed by the project owner (2026-11-09):

- LB443-LB448 and LB467-LB469 are laboratory rooms.
- LB483-LB486 are lecture rooms.

Rooms are entered by hand, so this only corrects rows that already exist.
It does not create missing rooms, because their capacities have not been
confirmed. Add those on the Rooms page.

Downgrade is a no-op: this is a data correction, and the previous values were
wrong.

Revision ID: c3a1f0e2d4b6
Revises: a9c1e3f5b7d2
"""

import sqlalchemy as sa
from alembic import op

revision = "c3a1f0e2d4b6"
down_revision = "a9c1e3f5b7d2"
branch_labels = None
depends_on = None

LAB_ROOMS = [f"LB{n}" for n in (*range(443, 449), *range(467, 470))]
LECTURE_ROOMS = [f"LB{n}" for n in range(483, 487)]


def upgrade() -> None:
    statement = sa.text(
        "UPDATE room SET room_type = :room_type WHERE room_code IN :codes"
    ).bindparams(sa.bindparam("codes", expanding=True))
    op.execute(statement.bindparams(room_type="laboratory", codes=LAB_ROOMS))
    op.execute(statement.bindparams(room_type="lecture", codes=LECTURE_ROOMS))


def downgrade() -> None:
    pass
