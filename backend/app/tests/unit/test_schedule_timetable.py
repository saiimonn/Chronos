"""CHR-81 / CHR-VIEW-002 timetable projection ordering tests."""

from datetime import time
from uuid import UUID

from app.services.schedules import _meeting_days_sort_key, _timetable_sort_key


def test_meeting_day_sort_key_follows_the_approved_week() -> None:
    patterns = [None, "Sa", "TTh", "F", "MWF", "Th"]

    assert sorted(patterns, key=_meeting_days_sort_key) == [
        "MWF",
        "TTh",
        "Th",
        "F",
        "Sa",
        None,
    ]


def _row(
    record_number: int,
    meeting_days: str = "M",
    start_time: time = time(8),
    **overrides,
) -> dict:
    identity = UUID(f"00000000-0000-0000-0000-{record_number:012d}")
    row = {
        "record_id": identity,
        "meeting_days": meeting_days,
        "start_time": start_time,
        "program_code": "<PROGRAM-1>",
        "year_level": 1,
        "section_letter": "A",
        "block_id": identity,
        "subject_code": "<SUBJECT-1>",
        "group_no": 1,
        "offering_id": identity,
        "room_code": "<ROOM-1>",
    }
    row.update(overrides)
    return row


def test_timetable_sort_key_orders_start_time_after_first_meeting_day() -> None:
    monday_late = _row(1, "MWF", time(10))
    monday_early = _row(2, "M", time(8))
    tuesday_earlier = _row(3, "TTh", time(7, 30))

    rows = [tuesday_earlier, monday_late, monday_early]

    assert sorted(rows, key=_timetable_sort_key) == [
        monday_early,
        monday_late,
        tuesday_earlier,
    ]


def test_timetable_sort_key_orders_block_offering_and_room() -> None:
    block_id = UUID("00000000-0000-0000-0000-000000000010")
    offering_id = UUID("00000000-0000-0000-0000-000000000011")
    first = _row(1, block_id=block_id, offering_id=offering_id)
    later_block = _row(2, section_letter="B")
    later_offering = _row(3, block_id=block_id, group_no=2)
    later_room = _row(
        4, block_id=block_id, offering_id=offering_id, room_code="<ROOM-2>"
    )

    ordered = sorted(
        [later_room, later_offering, later_block, first], key=_timetable_sort_key
    )

    assert ordered == [
        first,
        later_room,
        later_offering,
        later_block,
    ]
