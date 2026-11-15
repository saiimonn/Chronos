from datetime import time, timedelta
from uuid import UUID, uuid4

from app.domain import (
    DCISM_CALENDAR,
    CalendarTemplate,
    SubjectType,
    Weekday,
    generate_windows,
)


def test_the_approved_calendar_is_monday_to_sunday_0730_to_2100():
    assert DCISM_CALENDAR.teaching_days == (
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    )
    assert DCISM_CALENDAR.opening_time == time(7, 30)
    assert DCISM_CALENDAR.closing_time == time(21, 0)
    assert DCISM_CALENDAR.interval == timedelta(minutes=30)


def test_the_grid_is_forty_eight_windows_split_by_type():
    windows = generate_windows()

    assert len(windows) == 48
    assert sum(w.subject_type is SubjectType.LEC for w in windows) == 25
    assert sum(w.subject_type is SubjectType.LAB for w in windows) == 23


def test_lecture_windows_come_first_and_bound_the_day():
    lectures = [w for w in generate_windows() if w.subject_type is SubjectType.LEC]

    assert (lectures[0].start_time, lectures[0].end_time) == (time(7, 30), time(9, 0))
    assert (lectures[-1].start_time, lectures[-1].end_time) == (
        time(19, 30),
        time(21, 0),
    )
    assert [w.ordinal for w in lectures] == list(range(25))


def test_laboratory_windows_follow_and_bound_the_day():
    labs = [w for w in generate_windows() if w.subject_type is SubjectType.LAB]

    assert (labs[0].start_time, labs[0].end_time) == (time(7, 30), time(10, 0))
    assert (labs[-1].start_time, labs[-1].end_time) == (time(18, 30), time(21, 0))
    assert [w.ordinal for w in labs] == list(range(25, 48))


def test_no_window_escapes_the_configured_day_bounds():
    windows = generate_windows()

    assert min(w.start_time for w in windows) == DCISM_CALENDAR.opening_time
    assert max(w.end_time for w in windows) == DCISM_CALENDAR.closing_time
    assert all(w.start_time >= DCISM_CALENDAR.opening_time for w in windows)
    assert all(w.end_time <= DCISM_CALENDAR.closing_time for w in windows)


def test_every_start_sits_on_a_thirty_minute_boundary():
    for window in generate_windows():
        assert window.start_time.minute in (0, 30)
        assert window.start_time.second == 0


def test_ordinals_are_contiguous_and_unique():
    assert [w.ordinal for w in generate_windows()] == list(range(48))


def test_each_window_carries_its_duration_from_the_subject_type():
    for window in generate_windows():
        minutes = (
            window.end_time.hour * 60
            + window.end_time.minute
            - window.start_time.hour * 60
            - window.start_time.minute
        )
        assert minutes == window.subject_type.duration_minutes


def test_labels_are_unique_and_name_the_window():
    windows = generate_windows()
    labels = [w.label for w in windows]

    assert labels[0] == "LEC 07:30-09:00"
    assert labels[25] == "LAB 07:30-10:00"
    assert len(set(labels)) == len(labels)
    # The column is String(50) with a per-term uniqueness constraint.
    assert all(len(label) <= 50 for label in labels)


def test_slot_ids_are_stable_for_a_term_and_distinct_across_terms():
    term_id = uuid4()
    other_term_id = uuid4()
    windows = generate_windows()

    first = [w.slot_id(term_id) for w in windows]
    second = [w.slot_id(term_id) for w in windows]
    other = [w.slot_id(other_term_id) for w in windows]

    assert first == second
    assert all(isinstance(slot_id, UUID) for slot_id in first)
    assert len(set(first)) == 48
    assert set(first).isdisjoint(other)


def test_generation_is_a_pure_function_of_its_calendar():
    shorter_day = CalendarTemplate(
        teaching_days=DCISM_CALENDAR.teaching_days,
        opening_time=time(7, 30),
        closing_time=time(10, 0),
        interval=timedelta(minutes=30),
    )

    windows = generate_windows(shorter_day)

    assert [(w.subject_type, w.start_time) for w in windows] == [
        (SubjectType.LEC, time(7, 30)),
        (SubjectType.LEC, time(8, 0)),
        (SubjectType.LEC, time(8, 30)),
        (SubjectType.LAB, time(7, 30)),
    ]
