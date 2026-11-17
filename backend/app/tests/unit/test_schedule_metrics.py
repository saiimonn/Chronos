"""Schedule quality metrics: hand-checked numbers on the GA fixture term."""

from dataclasses import replace

import pytest

from app.domain import Assignment
from app.services.schedule_metrics import compute_schedule_metrics
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    SECTION_LAB_ID,
    SECTION_LECTURE_ID,
    SLOT_LAB_ID,
    SLOT_MORNING_ID,
    make_ga_problem,
)


def _problem():
    problem = make_ga_problem(include_unstaffable=False)
    return replace(problem, term=replace(problem.term, term_number=1))


LECTURE = Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "MW")
LAB = Assignment(SECTION_LAB_ID, FACULTY_FLEXIBLE_ID, SLOT_LAB_ID, LABORATORY_ROOM, "TTh")


def test_room_frequency_occupancy_and_utilization():
    problem = _problem()
    metrics = compute_schedule_metrics(problem, (LECTURE, LAB))
    rooms = {item.room_type: item for item in metrics.rooms}
    seats = {c.class_id: c.capacity for c in problem.classes}
    capacity = {r.room_code: r.capacity for r in problem.rooms}

    # Grid 08:00-17:30 = 9.5h a day, Monday-Friday in a semester.
    assert metrics.window_hours_per_day == 9.5
    assert metrics.class_days == 5
    lecture_rooms = sum(1 for r in problem.rooms if r.room_type.value == "lecture")
    # The lecture meets 1h30 on Monday and Wednesday: 3 room-hours.
    assert rooms["lecture"].frequency.numerator == pytest.approx(3)
    assert rooms["lecture"].frequency.denominator == pytest.approx(9.5 * 5 * lecture_rooms)
    assert rooms["lecture"].occupancy.value == pytest.approx(
        seats[SECTION_LECTURE_ID] / capacity[LECTURE_ROOM]
    )
    # The lab meets 2h30 on Tuesday and Thursday: 5 room-hours in one lab.
    assert rooms["laboratory"].frequency.value == pytest.approx(5 / (9.5 * 5))
    assert rooms["laboratory"].rooms_used == 1
    assert rooms["all"].utilization == pytest.approx(
        rooms["all"].frequency.value * rooms["all"].occupancy.value
    )


def test_faculty_load_counts_units_once_per_offering():
    problem = _problem()
    metrics = compute_schedule_metrics(problem, (LECTURE, LAB))
    member = next(m for m in problem.faculty if m.faculty_id == FACULTY_FLEXIBLE_ID)

    assert metrics.faculty.faculty_teaching == 1
    assert metrics.faculty.load.numerator == 6
    assert metrics.faculty.mean_load_ratio == pytest.approx(6 / problem.limits.max_workload(member))
    assert metrics.faculty.load_cv == 0
    assert metrics.faculty.overloaded == 0
    # Four teaching days (M, T, W, Th), one class each: no idle time.
    assert metrics.faculty.mean_teaching_days == 4
    assert metrics.faculty.mean_idle_hours_per_day == 0


def test_preference_satisfaction_counts_only_subjects_someone_prefers():
    problem = _problem()
    lecture_subject = next(c.subject_code for c in problem.classes if c.class_id == SECTION_LECTURE_ID)
    problem = replace(
        problem,
        faculty=tuple(
            replace(m, preferred_subjects=frozenset({lecture_subject}))
            if m.faculty_id == FACULTY_FLEXIBLE_ID
            else m
            for m in problem.faculty
        ),
    )
    metrics = compute_schedule_metrics(problem, (LECTURE, LAB))
    assert (metrics.faculty.preference.numerator, metrics.faculty.preference.denominator) == (1, 1)


def test_time_distribution_and_empty_schedule():
    problem = _problem()
    metrics = compute_schedule_metrics(problem, (LECTURE, LAB))
    assert metrics.time.sessions == 2
    assert metrics.time.evening.numerator == 0
    assert dict(metrics.time.starts_by_slot)[
        next(s.label for s in problem.time_slots if s.time_slot_id == SLOT_MORNING_ID)
    ] == 1

    empty = compute_schedule_metrics(problem, ())
    assert empty.time.sessions == 0
    assert empty.rooms[0].frequency.value == 0
    assert empty.rooms[0].occupancy.value is None
    assert empty.faculty.mean_load_ratio is None
