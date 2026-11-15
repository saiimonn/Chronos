from datetime import time
from uuid import uuid4

from app.domain import TimeSlotDTO, Weekday
from app.services.catalog.history_defaults import (
    infer_defaults,
    meeting_days_of,
    needs_mask,
)

TERM = uuid4()


def _slot(ordinal: int, start: time, end: time) -> TimeSlotDTO:
    return TimeSlotDTO(uuid4(), TERM, f"S{ordinal}", start, end, ordinal)


EARLY = _slot(0, time(7, 30), time(9, 0))
MID = _slot(1, time(10, 0), time(11, 30))
LATE = _slot(2, time(19, 30), time(21, 0))
MID_LAB = _slot(3, time(9, 0), time(11, 30))
SLOTS = [EARLY, MID, LATE, MID_LAB]


def test_days_taught_at_least_half_as_often_as_the_busiest_are_preferred():
    # Tue/Thu 3 classes each, Mon 1, Wed 1: Mon and Wed fall below half of 3.
    records = [("TTh", "10:00-11:30")] * 3 + [("M", "10:00-11:30"), ("W", "10:00-11:30")]
    assert infer_defaults(records, SLOTS).preferred_days == (
        Weekday.TUESDAY,
        Weekday.THURSDAY,
    )


def test_every_day_of_a_pattern_counts_including_weekends():
    assert infer_defaults([("F", "10:00-11:30")], SLOTS).preferred_days == (
        Weekday.FRIDAY,
    )
    both = [("MW", "10:00-11:30"), ("TTh", "10:00-11:30")]
    assert infer_defaults(both, SLOTS).preferred_days == (
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
    )
    result = infer_defaults([("Sa", "10:00-11:30"), ("MWF", "10:00-11:30")], SLOTS)
    assert result.preferred_days == (
        Weekday.MONDAY,
        Weekday.WEDNESDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
    )
    assert infer_defaults([("Su", "10:00-11:30")], SLOTS).preferred_days == (
        Weekday.SUNDAY,
    )


def test_unreadable_day_codes_count_for_nothing():
    assert meeting_days_of("TTh") == {Weekday.TUESDAY, Weekday.THURSDAY}
    assert meeting_days_of("MTWThF") == {
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
    }
    assert meeting_days_of("TBA") == frozenset()
    assert infer_defaults([("TBA", "10:00-11:30")], SLOTS).preferred_days == ()


def test_range_ignores_one_off_extremes():
    # Twenty mid-morning classes and one late-night class: the late slot stays out.
    records = [("MW", "09:00-11:30")] * 20 + [("MW", "19:30-21:00")]
    result = infer_defaults(records, SLOTS)
    assert result.time_range == ("09:00", "11:30")
    assert result.slot_ids == {MID.time_slot_id, MID_LAB.time_slot_id}


def test_no_history_gives_nothing():
    result = infer_defaults([], SLOTS)
    assert result == (result.__class__((), None, frozenset()))


def test_only_blank_masks_are_filled():
    assert needs_mask("")
    assert needs_mask("0000")
    assert needs_mask("1111")
    assert not needs_mask("0100")
