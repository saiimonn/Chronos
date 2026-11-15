"""The approved local teaching calendar and the meeting windows it implies.

A term's slot grid is not typed in; it is computed here. Keeping the
computation pure — no session, no model — means the grid can be asserted
against fixed inputs and reproduced identically by a service, a migration, or a
test without any of them agreeing on anything but this module.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.scheduling import SubjectType


class Weekday(StrEnum):
    MONDAY = "MON"
    TUESDAY = "TUE"
    WEDNESDAY = "WED"
    THURSDAY = "THU"
    FRIDAY = "FRI"
    SATURDAY = "SAT"
    SUNDAY = "SUN"


@dataclass(frozen=True, slots=True)
class CalendarTemplate:
    """The teaching days and hours a term's meeting windows are cut from."""

    teaching_days: tuple[Weekday, ...]
    opening_time: time
    closing_time: time
    interval: timedelta


DCISM_CALENDAR = CalendarTemplate(
    teaching_days=(
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    ),
    opening_time=time(7, 30),
    closing_time=time(21, 0),
    interval=timedelta(minutes=30),
)

# Lectures first, then laboratories. This order fixes the ordinals, and an
# ordinal is the position of a character in a stored availability mask, so
# reordering here silently redefines every mask ever saved. A duration added
# later must be appended, never inserted.
CANONICAL_DURATION_ORDER: tuple[SubjectType, ...] = (
    SubjectType.LEC,
    SubjectType.LAB,
)

# Slot identifiers are derived rather than random, so regenerating a term's grid
# reproduces them exactly and a gene, an edit, or a locked assignment keeps
# pointing at the same window across runs.
SLOT_NAMESPACE = uuid5(NAMESPACE_URL, "https://chronos.dcism.local/time-slot")

# A `time` carries no date, so the arithmetic runs against an arbitrary fixed one.
_ANCHOR_DATE = date(2000, 1, 1)

# Academic years are stored as the label the department writes, "YYYY-YYYY", not
# as a pair of integers. Comparing two of them as plain strings is only correct
# because the format is fixed-width and zero-padded, which this pattern enforces.
_ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{4})$")


def academic_year_start(academic_year: str) -> int:
    """The opening year of an "YYYY-YYYY" label, e.g. 2023 for "2023-2024".

    Raises `ValueError` rather than guessing. A year that cannot be read is a
    data fault, and silently treating it as some default would place a cohort
    on a prospectus nobody chose for it.
    """

    match = _ACADEMIC_YEAR.fullmatch(academic_year)
    if match is None:
        raise ValueError(
            f"academic year must use YYYY-YYYY format: {academic_year!r}"
        )
    start_year, end_year = (int(part) for part in match.groups())
    if end_year != start_year + 1:
        raise ValueError(
            f"academic year must cover consecutive years: {academic_year!r}"
        )
    return start_year


def format_academic_year(start_year: int) -> str:
    """The stored label for an opening year, e.g. "2023-2024" for 2023."""

    return f"{start_year}-{start_year + 1}"


def entry_academic_year(academic_year: str, year_level: int) -> str:
    """The year a cohort at `year_level` during `academic_year` entered.

    A cohort follows the prospectus in force when it enrolled, so this is how a
    Block reaches its curriculum: a fourth-year Block in 2026-2027 entered in
    2023-2024. The arithmetic assumes an unbroken progression of one year level
    per academic year, which is what a Block models — an individual student who
    is off-sequence is an irregular case and is not scheduled as a cohort.
    """

    if year_level < 1:
        raise ValueError(f"year level must be at least 1: {year_level!r}")
    return format_academic_year(academic_year_start(academic_year) - (year_level - 1))


@dataclass(frozen=True, slots=True)
class CanonicalWindow:
    """One meeting window on the grid, identified by its position."""

    subject_type: SubjectType
    start_time: time
    end_time: time
    ordinal: int

    @property
    def label(self) -> str:
        return (
            f"{self.subject_type.value} "
            f"{self.start_time:%H:%M}-{self.end_time:%H:%M}"
        )

    def slot_id(self, term_id: UUID) -> UUID:
        return uuid5(
            SLOT_NAMESPACE,
            f"{term_id}:{self.subject_type.value}:{self.start_time:%H:%M}",
        )


def generate_windows(
    calendar: CalendarTemplate = DCISM_CALENDAR,
    durations: Sequence[SubjectType] = CANONICAL_DURATION_ORDER,
) -> tuple[CanonicalWindow, ...]:
    """Every window of each duration that fits inside the calendar's day.

    A window is emitted only while it ends on or before closing time, so
    staying inside the day's bounds is a property of the construction rather
    than a rule applied to the result afterwards.
    """

    closing = datetime.combine(_ANCHOR_DATE, calendar.closing_time)
    windows: list[CanonicalWindow] = []
    for subject_type in durations:
        cursor = datetime.combine(_ANCHOR_DATE, calendar.opening_time)
        while cursor + subject_type.duration <= closing:
            end = cursor + subject_type.duration
            windows.append(
                CanonicalWindow(
                    subject_type=subject_type,
                    start_time=cursor.time(),
                    end_time=end.time(),
                    ordinal=len(windows),
                )
            )
            cursor += calendar.interval
    return tuple(windows)


def normalize_preferred_days(
    days: Sequence[Weekday | str],
    calendar: CalendarTemplate = DCISM_CALENDAR,
) -> tuple[Weekday, ...]:
    """A faculty member's preferred days, deduplicated, in calendar order.

    Only the calendar's teaching days can be preferred; anything else raises
    `ValueError` rather than being dropped, so a typo is never stored as "no
    preference".
    """

    chosen = {Weekday(day) for day in days}
    outside = sorted(day.value for day in chosen - set(calendar.teaching_days))
    if outside:
        raise ValueError(f"not a teaching day: {', '.join(outside)}")
    return tuple(day for day in calendar.teaching_days if day in chosen)


def encode_preferred_days(days: Sequence[Weekday | str]) -> str:
    """The stored form of a preferred-day set, e.g. "MON,WED,FRI"."""

    return ",".join(day.value for day in normalize_preferred_days(days))


def decode_preferred_days(stored: str | None) -> tuple[Weekday, ...]:
    """Read a stored preferred-day value; empty (or unset) means no preference.

    Unknown codes are skipped rather than raised, so a reader never fails on
    a value written under an older calendar.
    """

    known = {day.value for day in Weekday}
    return tuple(
        Weekday(code)
        for code in (stored or "").split(",")
        if code in known
    )
