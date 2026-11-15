"""Restrict LAB subjects to the department's lab rooms.

Confirmed by the project owner (2026-11-13):

- Networking labs meet only in LB443, LB469 and LB470.
- Every other LAB subject meets only in LB445-LB448, LB467 and LB468.

Only LAB subjects with no allowed rooms yet are touched, so a choice already
made on the Subjects page is kept. Only rooms that exist and are active are
added: rooms are entered by hand, and a retired room must not be chosen.

Downgrade is a no-op: this is a data correction.

Revision ID: b8d2f4a6c0e1
Revises: f2a4c6e8b0d1
"""

import sqlalchemy as sa
from alembic import op

revision = "b8d2f4a6c0e1"
down_revision = "f2a4c6e8b0d1"
branch_labels = None
depends_on = None

NETWORKING_ROOMS = ["LB443", "LB469", "LB470"]
OTHER_LAB_ROOMS = ["LB445", "LB446", "LB447", "LB448", "LB467", "LB468"]


def upgrade() -> None:
    statement = sa.text(
        """
        INSERT INTO subject_allowed_room (subject_code, room_code)
        SELECT s.subject_code, r.room_code
        FROM subject s
        JOIN room r ON r.room_code IN :codes AND r.is_active
        WHERE s.subject_type = 'LAB'
          AND (s.subject_title ILIKE '%NETWORKING%') = :networking
          AND NOT EXISTS (
              SELECT 1 FROM subject_allowed_room a
              WHERE a.subject_code = s.subject_code
          )
        """
    ).bindparams(sa.bindparam("codes", expanding=True))
    op.execute(statement.bindparams(codes=OTHER_LAB_ROOMS, networking=False))
    op.execute(statement.bindparams(codes=NETWORKING_ROOMS, networking=True))


def downgrade() -> None:
    pass
