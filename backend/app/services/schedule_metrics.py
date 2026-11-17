"""Schedule quality and resource-utilization metrics for one schedule.

Every metric is a plain ratio or statistic over the schedule's assignments
and the term's catalog, reported with its numerator and denominator so the
thesis can state exactly how it was computed. The definitions follow the
timetabling literature named in ``METRIC_SOURCES``.

Counting rules shared by every metric:

* A *session* is one Offering's placement. Paired Blocks attend a shared
  lecture together, so it counts once for room use and teaching load (the
  validator counts load the same way) and its students are summed.
* Students are approximated by Block capacity (20, or 25 expanded): the
  catalog records seats per Block, not enrolment.
* The weekly teaching window is the time-slot grid (earliest start to latest
  end) on the term's class days: Monday-Friday in a semester, Monday-Thursday
  in the summer term. Weekend meetings are reported but not counted against
  the window.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import time
from statistics import mean, pstdev
from uuid import UUID

from app.domain import Assignment, RoomType, SchedulingProblem
from app.domain.calendar import Weekday
from app.domain.meeting_patterns import (
    is_summer_term,
    meeting_end,
    pattern_weekdays,
)
from app.services.ga.day_balance import cohort_day_minutes, spread_hours

SEMESTER_DAYS = (
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
)
SUMMER_DAYS = SEMESTER_DAYS[:4]
# A Block's week counts as balanced when its heaviest and lightest
# Monday-Thursday days differ by at most this many hours.
BALANCED_SPREAD_HOURS = 1.5
# Sessions starting at or after this time count as evening classes.
EVENING_START = time(17, 0)


@dataclass(frozen=True, slots=True)
class Ratio:
    numerator: float
    denominator: float

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True, slots=True)
class RoomMetrics:
    room_type: str  # "all", "lecture" or "laboratory"
    rooms_total: int
    rooms_used: int
    frequency: Ratio  # room-hours used / room-hours available
    occupancy: Ratio  # seat-hours filled / seat-hours offered, while in use
    utilization: float | None  # frequency x occupancy


@dataclass(frozen=True, slots=True)
class FacultyMetrics:
    faculty_active: int
    faculty_teaching: int
    load: Ratio  # units assigned / maximum units, over active faculty
    mean_load_ratio: float | None  # per teaching member: units / maximum
    load_cv: float | None  # coefficient of variation of the load ratio
    overloaded: int
    preference: Ratio  # sessions taught by a preferring teacher / sessions with one
    # Fragmentation (thesis p.33: "minimize fragmented teaching days"), over
    # teaching members: days a week on campus, and idle hours between their
    # own classes on a teaching day.
    mean_teaching_days: float | None = None
    mean_idle_hours_per_day: float | None = None


@dataclass(frozen=True, slots=True)
class BlockMetrics:
    blocks_scheduled: int
    mean_spread_hours: float | None
    balanced: Ratio  # Blocks with spread <= BALANCED_SPREAD_HOURS / Blocks
    mean_idle_hours_per_day: float | None
    mean_days_per_week: float | None
    max_daily_hours: float | None


@dataclass(frozen=True, slots=True)
class TimeMetrics:
    sessions: int
    evening: Ratio  # sessions starting at/after EVENING_START / sessions
    weekend: Ratio  # sessions meeting on a weekend day / sessions
    starts_by_slot: list[tuple[str, int]] = field(default_factory=list)
    peak_slot_share: float | None = None


@dataclass(frozen=True, slots=True)
class ScheduleMetrics:
    rooms: list[RoomMetrics]
    faculty: FacultyMetrics
    blocks: BlockMetrics
    time: TimeMetrics
    window_hours_per_day: float
    class_days: int


@dataclass(frozen=True, slots=True)
class _Session:
    assignment: Assignment
    subject_code: str
    students: int
    start: time
    end: time
    days: frozenset[Weekday]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _sessions(problem: SchedulingProblem, assignments: Sequence[Assignment]) -> list[_Session]:
    sections = {item.class_id: item for item in problem.classes}
    slots = {slot.time_slot_id: slot for slot in problem.time_slots}
    grouped: dict[tuple, list[Assignment]] = defaultdict(list)
    for item in assignments:
        section = sections.get(item.class_id)
        if section is None or item.time_slot_id not in slots or not item.meeting_days:
            continue
        key = (
            section.offering_id,
            item.faculty_id,
            item.room_code,
            item.time_slot_id,
            item.meeting_days,
        )
        grouped[key].append(item)
    sessions = []
    for items in grouped.values():
        first = items[0]
        slot = slots[first.time_slot_id]
        try:
            days = pattern_weekdays(first.meeting_days or "")
        except ValueError:
            continue
        sessions.append(
            _Session(
                assignment=first,
                subject_code=sections[first.class_id].subject_code,
                students=sum(sections[i.class_id].capacity for i in items),
                start=slot.start_time,
                end=meeting_end(slot, first.meeting_days),
                days=days,
            )
        )
    return sessions


def _room_metrics(
    problem: SchedulingProblem,
    sessions: list[_Session],
    class_days: Sequence[Weekday],
    window_minutes: int,
    room_type: RoomType | None,
) -> RoomMetrics:
    rooms = [
        room
        for room in problem.rooms
        if room.is_active and (room_type is None or room.room_type is room_type)
    ]
    capacity = {room.room_code: room.capacity for room in rooms}
    used_minutes = 0.0
    filled_seat_minutes = 0.0
    offered_seat_minutes = 0.0
    used_rooms: set[str] = set()
    for session in sessions:
        code = session.assignment.room_code
        if code not in capacity:
            continue
        days_in_window = len([day for day in session.days if day in class_days])
        minutes = (_minutes(session.end) - _minutes(session.start)) * days_in_window
        if minutes <= 0:
            continue
        used_rooms.add(code)
        used_minutes += minutes
        filled_seat_minutes += minutes * min(session.students, capacity[code])
        offered_seat_minutes += minutes * capacity[code]
    available = window_minutes * len(class_days) * len(rooms)
    frequency = Ratio(used_minutes / 60, available / 60)
    occupancy = Ratio(filled_seat_minutes / 60, offered_seat_minutes / 60)
    utilization = (
        frequency.value * occupancy.value
        if frequency.value is not None and occupancy.value is not None
        else None
    )
    return RoomMetrics(
        room_type="all" if room_type is None else room_type.value,
        rooms_total=len(rooms),
        rooms_used=len(used_rooms),
        frequency=frequency,
        occupancy=occupancy,
        utilization=utilization,
    )


def _faculty_metrics(problem: SchedulingProblem, sessions: list[_Session]) -> FacultyMetrics:
    subjects = {subject.subject_code: subject for subject in problem.subjects}
    active = [member for member in problem.faculty if member.is_active]
    members = {member.faculty_id: member for member in problem.faculty}
    sections = {item.class_id: item for item in problem.classes}
    # Units count once per Offering a member teaches, as the validator does.
    units: dict[UUID, dict[UUID, int]] = defaultdict(dict)
    for session in sessions:
        offering = sections[session.assignment.class_id].offering_id
        units[session.assignment.faculty_id][offering] = subjects[session.subject_code].units
    load = {faculty_id: sum(by.values()) for faculty_id, by in units.items()}
    meetings: dict[tuple[UUID, Weekday], list[tuple[int, int]]] = defaultdict(list)
    for session in sessions:
        for day in session.days:
            meetings[(session.assignment.faculty_id, day)].append(
                (_minutes(session.start), _minutes(session.end))
            )
    days_by_member: dict[UUID, int] = defaultdict(int)
    idle_by_day: list[float] = []
    for (faculty_id, _day), items in meetings.items():
        items.sort()
        days_by_member[faculty_id] += 1
        idle_by_day.append(
            sum(max(0, b[0] - a[1]) for a, b in zip(items, items[1:])) / 60
        )
    cap = {m.faculty_id: problem.limits.max_workload(m) for m in active}
    teaching = [member for member in active if load.get(member.faculty_id)]
    ratios = [load[m.faculty_id] / cap[m.faculty_id] for m in teaching]
    mean_ratio = mean(ratios) if ratios else None
    cv = pstdev(ratios) / mean_ratio if ratios and mean_ratio else None
    preferring_subjects = {
        code for member in active for code in member.preferred_subjects
    }
    with_preference = [s for s in sessions if s.subject_code in preferring_subjects]
    satisfied = [
        s
        for s in with_preference
        if s.assignment.faculty_id in members
        and s.subject_code in members[s.assignment.faculty_id].preferred_subjects
    ]
    return FacultyMetrics(
        faculty_active=len(active),
        faculty_teaching=len(teaching),
        load=Ratio(
            sum(load.get(m.faculty_id, 0) for m in active),
            sum(cap.values()),
        ),
        mean_load_ratio=mean_ratio,
        load_cv=cv,
        overloaded=sum(1 for m in active if load.get(m.faculty_id, 0) > cap[m.faculty_id]),
        preference=Ratio(len(satisfied), len(with_preference)),
        mean_teaching_days=mean(days_by_member.values()) if days_by_member else None,
        mean_idle_hours_per_day=mean(idle_by_day) if idle_by_day else None,
    )


def _block_metrics(
    problem: SchedulingProblem,
    assignments: Sequence[Assignment],
    sessions_by_class: dict[UUID, _Session],
) -> BlockMetrics:
    loads = cohort_day_minutes(problem, assignments)
    spreads = [spread_hours(load) for load in loads.values()]
    sections = {item.class_id: item for item in problem.classes}
    by_cohort_day: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for item in assignments:
        session = sessions_by_class.get(item.class_id)
        section = sections.get(item.class_id)
        if session is None or section is None:
            continue
        for day in session.days:
            by_cohort_day[(section.cohort_key, day)].append(
                (_minutes(session.start), _minutes(session.end))
            )
    idle_per_day = []
    daily_hours = []
    days_per_cohort: dict[tuple, int] = defaultdict(int)
    for (cohort, _day), meetings in by_cohort_day.items():
        meetings.sort()
        idle = sum(
            max(0, nxt[0] - prev[1]) for prev, nxt in zip(meetings, meetings[1:])
        )
        idle_per_day.append(idle / 60)
        daily_hours.append(sum(end - start for start, end in meetings) / 60)
        days_per_cohort[cohort] += 1
    return BlockMetrics(
        blocks_scheduled=len(loads),
        mean_spread_hours=mean(spreads) if spreads else None,
        balanced=Ratio(sum(1 for s in spreads if s <= BALANCED_SPREAD_HOURS), len(spreads)),
        mean_idle_hours_per_day=mean(idle_per_day) if idle_per_day else None,
        mean_days_per_week=mean(days_per_cohort.values()) if days_per_cohort else None,
        max_daily_hours=max(daily_hours) if daily_hours else None,
    )


def _time_metrics(problem: SchedulingProblem, sessions: list[_Session]) -> TimeMetrics:
    weekend = {Weekday.SATURDAY, Weekday.SUNDAY}
    slots = sorted(problem.time_slots, key=lambda slot: slot.ordinal)
    starts: dict[UUID, int] = defaultdict(int)
    for session in sessions:
        starts[session.assignment.time_slot_id] += 1
    by_slot = [(slot.label, starts.get(slot.time_slot_id, 0)) for slot in slots]
    peak = max((count for _, count in by_slot), default=0)
    return TimeMetrics(
        sessions=len(sessions),
        evening=Ratio(sum(1 for s in sessions if s.start >= EVENING_START), len(sessions)),
        weekend=Ratio(sum(1 for s in sessions if s.days & weekend), len(sessions)),
        starts_by_slot=by_slot,
        peak_slot_share=peak / len(sessions) if sessions else None,
    )


def compute_schedule_metrics(
    problem: SchedulingProblem,
    assignments: Sequence[Assignment],
) -> ScheduleMetrics:
    sessions = _sessions(problem, assignments)
    class_days = SUMMER_DAYS if is_summer_term(problem.term.term_number) else SEMESTER_DAYS
    if problem.time_slots:
        window = max(_minutes(s.end_time) for s in problem.time_slots) - min(
            _minutes(s.start_time) for s in problem.time_slots
        )
    else:
        window = 0
    sections = {item.class_id: item for item in problem.classes}
    by_key = {
        (sections[s.assignment.class_id].offering_id, s.assignment.faculty_id,
         s.assignment.room_code, s.assignment.time_slot_id, s.assignment.meeting_days): s
        for s in sessions
    }
    sessions_by_class = {}
    for item in assignments:
        section = sections.get(item.class_id)
        if section is None:
            continue
        session = by_key.get(
            (section.offering_id, item.faculty_id, item.room_code, item.time_slot_id, item.meeting_days)
        )
        if session is not None:
            sessions_by_class[item.class_id] = session
    return ScheduleMetrics(
        rooms=[
            _room_metrics(problem, sessions, class_days, window, room_type)
            for room_type in (None, RoomType.LECTURE, RoomType.LABORATORY)
        ],
        faculty=_faculty_metrics(problem, sessions),
        blocks=_block_metrics(problem, assignments, sessions_by_class),
        time=_time_metrics(problem, sessions),
        window_hours_per_day=window / 60,
        class_days=len(class_days),
    )
