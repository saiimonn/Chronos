"""How evenly a Block's teaching hours spread across Monday to Thursday.

A week of 4h on Monday-Wednesday and 3h on Tuesday-Thursday is easier on
students and teachers than 5h30 against 1h30, even though both hold the
same classes. The balance score rewards the smaller spread between the
heaviest and the lightest of those four days, per Block (cohort).

Friday and the weekend are left out on purpose: a Friday-only class holds a
whole week's sessions in one sitting, and most Blocks keep Friday light, so
counting it would push every class onto Friday or penalise every schedule.
In the summer term every class meets Monday to Thursday, so the spread is
always zero there and the score is a constant.
"""

from collections.abc import Hashable, Iterable, Mapping, Sequence
from functools import lru_cache

from app.domain import Assignment, SchedulingProblem
from app.domain.calendar import Weekday
from app.domain.meeting_patterns import meeting_duration, pattern_weekdays

BALANCED_WEEKDAYS: tuple[Weekday, ...] = (
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
)
# Weight of the day-balance score next to historical attainment in the GA's
# soft fitness component, for master-schedule (per-Block) generation. Both
# scores lie in [0, 1]. Placement and hard conflicts always rank first.
DEFAULT_DAY_BALANCE_WEIGHT = 0.5

DayLoad = dict[Weekday, float]


@lru_cache(maxsize=None)
def balanced_days_of(pattern: str) -> frozenset[Weekday]:
    """The Monday-Thursday days a pattern meets on ("F" has none)."""
    try:
        days = pattern_weekdays(pattern)
    except ValueError:
        return frozenset()
    return frozenset(day for day in days if day in BALANCED_WEEKDAYS)


def cohort_day_minutes(
    problem: SchedulingProblem,
    assignments: Iterable[Assignment],
) -> dict[Hashable, DayLoad]:
    """Minutes taught on each of Monday-Thursday, per cohort."""
    sections = {section.class_id: section for section in problem.classes}
    slots = {slot.time_slot_id: slot for slot in problem.time_slots}
    loads: dict[Hashable, DayLoad] = {}
    for assignment in assignments:
        section = sections.get(assignment.class_id)
        slot = slots.get(assignment.time_slot_id)
        if section is None or slot is None or not assignment.meeting_days:
            continue
        load = loads.setdefault(
            section.cohort_key, {day: 0.0 for day in BALANCED_WEEKDAYS}
        )
        minutes = meeting_duration(slot, assignment.meeting_days).total_seconds() / 60
        for day in balanced_days_of(assignment.meeting_days):
            load[day] += minutes
    return loads


def spread_hours(load: Mapping[Weekday, float]) -> float:
    """Heaviest minus lightest Monday-Thursday day, in hours."""
    values = [load.get(day, 0.0) for day in BALANCED_WEEKDAYS]
    return (max(values) - min(values)) / 60


def day_balance_score(
    problem: SchedulingProblem,
    assignments: Sequence[Assignment],
) -> float:
    """1.0 for a perfectly even week, falling toward 0 as the spread grows.

    Averaged over cohorts: 1 / (1 + spread in hours), so a 1h spread scores
    0.5, a 4h spread 0.2. A problem with nothing placed on Monday-Thursday
    scores 1.0 (nothing is unbalanced).
    """
    loads = cohort_day_minutes(problem, assignments)
    if not loads:
        return 1.0
    return sum(1.0 / (1.0 + spread_hours(load)) for load in loads.values()) / len(
        loads
    )


def lighter_patterns_first(
    patterns: Sequence[str],
    load: Mapping[Weekday, float],
) -> tuple[str, ...]:
    """``patterns`` with the ones meeting on the lighter days first.

    Patterns without a Monday-Thursday day (Friday, weekend) keep their
    relative order after the others; ties keep the configured order.
    """
    indexed = list(enumerate(patterns))

    def key(item: tuple[int, str]) -> tuple[int, float, int]:
        index, pattern = item
        days = balanced_days_of(pattern)
        if not days:
            return (1, 0.0, index)
        return (0, sum(load.get(day, 0.0) for day in days) / len(days), index)

    return tuple(pattern for _, pattern in sorted(indexed, key=key))
