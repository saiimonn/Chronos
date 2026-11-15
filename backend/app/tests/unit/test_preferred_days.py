import pytest

from app.domain import (
    Weekday,
    decode_preferred_days,
    encode_preferred_days,
    normalize_preferred_days,
)


def test_preferences_are_deduplicated_into_calendar_order():
    assert normalize_preferred_days(["SAT", "MON", Weekday.WEDNESDAY, "MON"]) == (
        Weekday.MONDAY,
        Weekday.WEDNESDAY,
        Weekday.SATURDAY,
    )
    assert encode_preferred_days(["FRI", "MON"]) == "MON,FRI"
    assert encode_preferred_days([]) == ""


@pytest.mark.parametrize("day", ["Monday", "XYZ"])
def test_only_teaching_days_can_be_preferred(day: str):
    with pytest.raises(ValueError):
        normalize_preferred_days([day])


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("", ()),
        (None, ()),
        ("TUE,THU", (Weekday.TUESDAY, Weekday.THURSDAY)),
        # A reader never fails on an unknown code.
        ("MON,??", (Weekday.MONDAY,)),
    ],
)
def test_stored_preferences_decode_leniently(stored, expected):
    assert decode_preferred_days(stored) == expected


def test_faculty_days_are_any_set_of_single_days():
    from app.domain import normalize_preferred_days

    assert normalize_preferred_days(["MON"]) == (Weekday.MONDAY,)
    assert normalize_preferred_days(["FRI", "MON", "WED"]) == (
        Weekday.MONDAY,
        Weekday.WEDNESDAY,
        Weekday.FRIDAY,
    )
    assert normalize_preferred_days(["SUN", "SAT"]) == (
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    )
    assert normalize_preferred_days([]) == ()
