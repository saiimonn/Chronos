"""Spell every room code one way: "<BUILDING CODE><NUMBER>" (e.g. "LB486").

Rooms were stored as "LB486" by the reference data, "LB 486" by the Rooms
form, and "LB LB486" when the whole code was typed into the form's room number
field. "LB486" is the department's spelling: historical records and mined rule
packages use it and the importers match room codes exactly. This rewrites
every code to it and moves the schedule records and mined rules that name the
room along with it.

- A code that does not start with its building's code followed by a number is
  left alone.
- If the corrected code already belongs to another room, the two rows are
  merged only when they describe the same room (building, type, expansion,
  capacity). The merged room is active if either was. Otherwise both are kept
  and a warning names them, to be resolved by hand.
- Each room is corrected in its own savepoint, so a conflict on one (for
  example two identical mined rules that would collide) skips that room
  without undoing the others.

Historical snapshot tables are immutable and keep the codes they recorded.

The spelling rule is copied here, not imported from app.domain, so later
changes to the application cannot change what this revision did.

Downgrade is a no-op: this is a data correction.

Revision ID: e7a9c1b3d5f8
Revises: d5f9b3e7a2c4
"""

import logging
import re

import sqlalchemy as sa
from alembic import op

revision = "e7a9c1b3d5f8"
down_revision = "d5f9b3e7a2c4"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

ROOM_CODE_MAX_LENGTH = 20
# Columns that must agree before two spellings are treated as one room.
SAME_ROOM = ("building_id", "building_name", "room_type", "expanded", "capacity")
REFERENCING = ("schedule_record", "extracted_rule")


def _canonical(building_code: str, room_code: str) -> str:
    code = room_code.strip().upper()
    building = building_code.strip().upper()
    if not building:
        return code
    prefix = re.escape(building)
    match = re.fullmatch(rf"{prefix}[\s-]*(?:{prefix}[\s-]*)?(\d.*)", code)
    if match is None:
        return code
    number = re.sub(r"\s+", "", match.group(1))
    return f"{building}{number}"


def _row(bind, room_code: str):
    return bind.execute(
        sa.text("SELECT * FROM room WHERE room_code = :code"), {"code": room_code}
    ).mappings().one_or_none()


def _repoint(bind, old: str, new: str) -> None:
    for table in REFERENCING:
        bind.execute(
            sa.text(f"UPDATE {table} SET room_code = :new WHERE room_code = :old"),
            {"new": new, "old": old},
        )


def upgrade() -> None:
    bind = op.get_bind()
    columns = [column["name"] for column in sa.inspect(bind).get_columns("room")]
    copied = ", ".join(f'"{name}"' for name in columns if name != "room_code")
    # A room's building is its building_id; rooms that predate the building
    # catalog may only carry the name, which the catalog matches case-blind.
    rooms = bind.execute(
        sa.text(
            "SELECT r.room_code, b.building_code FROM room r "
            "JOIN building b ON b.building_id = r.building_id "
            "OR (r.building_id IS NULL "
            "AND lower(b.building_name) = lower(btrim(r.building_name))) "
            "ORDER BY r.room_code"
        )
    ).all()

    for old, building_code in rooms:
        new = _canonical(building_code, old)
        if new == old:
            continue
        if len(new) > ROOM_CODE_MAX_LENGTH:
            log.warning("Room %r: %r is too long; left unchanged.", old, new)
            continue
        savepoint = bind.begin_nested()
        try:
            existing = _row(bind, new)
            if existing is None:
                bind.execute(
                    sa.text(
                        f'INSERT INTO room ("room_code", {copied}) '
                        f"SELECT :new, {copied} FROM room WHERE room_code = :old"
                    ),
                    {"new": new, "old": old},
                )
            else:
                current = _row(bind, old)
                if any(current[name] != existing[name] for name in SAME_ROOM):
                    log.warning(
                        "Rooms %r and %r differ; both kept. Resolve them by hand.",
                        old,
                        new,
                    )
                    savepoint.rollback()
                    continue
                if current["is_active"] and not existing["is_active"]:
                    bind.execute(
                        sa.text("UPDATE room SET is_active = true WHERE room_code = :new"),
                        {"new": new},
                    )
            _repoint(bind, old, new)
            bind.execute(
                sa.text("DELETE FROM room WHERE room_code = :old"), {"old": old}
            )
            savepoint.commit()
            log.info("Room %r is now %r.", old, new)
        except sa.exc.DBAPIError as error:
            savepoint.rollback()
            log.warning("Room %r left unchanged: %s", old, error.orig)


def downgrade() -> None:
    pass
