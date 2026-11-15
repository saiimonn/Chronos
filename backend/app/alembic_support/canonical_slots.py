"""The canonical grid as it stood when CHR-TERM-004 shipped, frozen.

A migration must keep doing what it did the day it ran, so it cannot import
``app.domain.calendar`` — that module is free to change. This is the snapshot
the migration and its tests share, and it is not edited afterwards: a later
grid change brings its own migration and its own snapshot.
"""

from datetime import date, datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

SLOT_NAMESPACE = uuid5(NAMESPACE_URL, "https://chronos.dcism.local/time-slot")

_ANCHOR_DATE = date(2000, 1, 1)
_OPENING = time(7, 30)
_CLOSING = time(21, 0)
_INTERVAL = timedelta(minutes=30)
_DURATIONS = (("LEC", timedelta(minutes=90)), ("LAB", timedelta(minutes=150)))


def _build() -> tuple[tuple[str, time, time, int], ...]:
    closing = datetime.combine(_ANCHOR_DATE, _CLOSING)
    rows: list[tuple[str, time, time, int]] = []
    for subject_type, duration in _DURATIONS:
        cursor = datetime.combine(_ANCHOR_DATE, _OPENING)
        while cursor + duration <= closing:
            rows.append(
                (subject_type, cursor.time(), (cursor + duration).time(), len(rows))
            )
            cursor += _INTERVAL
    return tuple(rows)


CANONICAL_WINDOWS = _build()
MASK_LENGTH = len(CANONICAL_WINDOWS)


def slot_id(term_id: UUID, subject_type: str, start_time: time) -> UUID:
    return uuid5(SLOT_NAMESPACE, f"{term_id}:{subject_type}:{start_time:%H:%M}")


def label(subject_type: str, start_time: time, end_time: time) -> str:
    return f"{subject_type} {start_time:%H:%M}-{end_time:%H:%M}"


def unplace_scheduled_records(connection) -> int:
    """Release every assignment pinned to a slot that is about to be deleted.

    A placement is all-or-nothing: ``ck_schedule_record_assignment_provenance``
    requires schedule, class, and slot to be set together or not at all, so a
    record cannot keep half of one. The windows it was placed into stop
    existing, so the assignment stops existing with them and the term is
    scheduled again against the canonical grid.

    Historical rows carry no provenance — they record a past offering as text —
    so nothing ARM mines is touched here.
    """

    result = connection.execute(
        sa.text(
            "UPDATE schedule_record "
            "SET schedule_id = NULL, class_id = NULL, time_slot_id = NULL "
            "WHERE time_slot_id IS NOT NULL"
        )
    )
    return result.rowcount


def regrid_terms(connection) -> None:
    """Replace every term's grid and reset availability, per the approved policy."""

    unplace_scheduled_records(connection)
    connection.execute(sa.text("DELETE FROM time_slot"))

    term_ids = connection.execute(sa.text("SELECT term_id FROM term")).scalars().all()
    insert = sa.text(
        "INSERT INTO time_slot "
        "(time_slot_id, term_id, label, start_time, end_time, ordinal) "
        "VALUES (:slot_id, :term_id, :label, :start_time, :end_time, :ordinal)"
    )
    for term_id in term_ids:
        for subject_type, start_time, end_time, ordinal in CANONICAL_WINDOWS:
            connection.execute(
                insert,
                {
                    "slot_id": slot_id(term_id, subject_type, start_time),
                    "term_id": term_id,
                    "label": label(subject_type, start_time, end_time),
                    "start_time": start_time,
                    "end_time": end_time,
                    "ordinal": ordinal,
                },
            )

    # Every stored mask addressed the old grid: its length is wrong and its
    # positions name different windows. It is reset rather than reinterpreted,
    # so readiness blocks until availability is entered against the new grid.
    connection.execute(
        sa.text("UPDATE faculty SET availability_mask = :mask"),
        {"mask": "0" * MASK_LENGTH},
    )
