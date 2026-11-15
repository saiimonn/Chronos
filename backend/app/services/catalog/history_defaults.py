"""Starting availability and preferred days for faculty, read from history.

Without this, every faculty member starts "open in every slot" with no
preferred days, and an administrator has to edit each one by hand. Most of
them have years of past classes that already show when and on which days
they teach, so that is used as the starting point instead.

- Preferred days: every day a past class met on counts once for that class
  ("MWF" counts for Monday, Wednesday and Friday). A day is preferred when it
  has at least half as many classes as the instructor's busiest day, so a day
  taught only now and then is left out.
- Availability: the usual teaching range, from the 10th-percentile start time
  to the 90th-percentile end time, so one odd 07:30 or 21:00 class does not
  stretch the range to the whole day. Every slot that fits inside the range is
  marked available.

Only blank values are filled: an unset, all-zero (the regrid reset) or
all-ones (the "open in every slot" default, saved as is) mask, and empty
preferred days. ``replace_days`` is the one exception: it recomputes preferred
days for everyone with history, for when the day rule itself changes. Anything an administrator saved is left alone, and
the review stamp is not touched, so the administrator still reviews the term.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import TimeSlotDTO, Weekday, encode_preferred_days
from app.models import Faculty, HistoricalRecord
from app.services.validation.availability import (
    AvailabilityMaskCodec,
    is_unset_mask,
)

# Workbook day codes; "Th", "Sa" and "Su" are tried before their one-letter
# prefixes so "TTh" reads as Tuesday and Thursday.
DAY_CODES: tuple[tuple[str, Weekday], ...] = (
    ("Th", Weekday.THURSDAY),
    ("Sa", Weekday.SATURDAY),
    ("Su", Weekday.SUNDAY),
    ("M", Weekday.MONDAY),
    ("T", Weekday.TUESDAY),
    ("W", Weekday.WEDNESDAY),
    ("F", Weekday.FRIDAY),
)
CALENDAR_ORDER: tuple[Weekday, ...] = (
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
    Weekday.SATURDAY,
    Weekday.SUNDAY,
)
# A day is preferred at this share of the busiest day's class count or more.
PREFERRED_DAY_SHARE = 0.5
START_PERCENTILE = 0.10
END_PERCENTILE = 0.90


@dataclass(frozen=True, slots=True)
class HistoryDefaults:
    preferred_days: tuple[Weekday, ...]
    # Usual teaching range as "HH:MM" strings, or None without timed history.
    time_range: tuple[str, str] | None
    slot_ids: frozenset[UUID]


def meeting_days_of(pattern: str) -> frozenset[Weekday]:
    """The days in a workbook pattern such as "TTh" or "MWF".

    An unreadable code yields no days rather than a guess.
    """
    days: set[Weekday] = set()
    offset = 0
    while offset < len(pattern):
        match = next(
            (pair for pair in DAY_CODES if pattern.startswith(pair[0], offset)), None
        )
        if match is None:
            return frozenset()
        days.add(match[1])
        offset += len(match[0])
    return frozenset(days)


def infer_defaults(
    records: Iterable[tuple[str, str]], slots: Sequence[TimeSlotDTO]
) -> HistoryDefaults:
    """Infer defaults from (meeting_days, "HH:MM-HH:MM") pairs of one instructor."""
    records = list(records)
    votes: Counter[Weekday] = Counter(
        day for meeting_days, _ in records for day in meeting_days_of(meeting_days)
    )
    busiest = max(votes.values(), default=0)
    preferred = tuple(
        day
        for day in CALENDAR_ORDER
        if busiest and votes[day] >= PREFERRED_DAY_SHARE * busiest
    )

    if not records:
        return HistoryDefaults(preferred, None, frozenset())
    # Zero-padded "HH:MM" strings sort in time order.
    starts = sorted(time_slot.split("-")[0] for _, time_slot in records)
    ends = sorted(time_slot.split("-")[1] for _, time_slot in records)
    last = len(records) - 1
    low = starts[math.floor(START_PERCENTILE * last)]
    high = ends[math.ceil(END_PERCENTILE * last)]
    slot_ids = frozenset(
        slot.time_slot_id
        for slot in slots
        if f"{slot.start_time:%H:%M}" >= low and f"{slot.end_time:%H:%M}" <= high
    )
    return HistoryDefaults(preferred, (low, high), slot_ids)


def needs_mask(mask: str) -> bool:
    # All-ones is what saving the "open in every slot" default stores, so it is
    # the default too, not a narrowed selection.
    return is_unset_mask(mask) or "1" not in mask or "0" not in mask


@dataclass(frozen=True, slots=True)
class FacultyFill:
    faculty_id: UUID
    full_name: str
    defaults: HistoryDefaults
    fill_mask: bool
    fill_days: bool
    # The stored value before this run, e.g. "MON,WED"; "" when blank.
    previous_days: str = ""


def fill_from_history(
    db: Session,
    slots: Sequence[TimeSlotDTO],
    *,
    apply: bool,
    replace_days: bool = False,
) -> list[FacultyFill]:
    """Plan (and with ``apply``, write) history defaults for blank faculty rows.

    ``slots`` is the active term's grid. The caller holds the term lock and
    commits; faculty rows are locked here, after the term, in the same order
    the availability endpoints use.
    """
    history: dict[UUID, list[tuple[str, str]]] = defaultdict(list)
    for faculty_id, meeting_days, time_slot in db.execute(
        select(
            HistoricalRecord.source_faculty_id,
            HistoricalRecord.meeting_days,
            HistoricalRecord.time_slot,
        ).where(HistoricalRecord.meeting_days.is_not(None))
    ):
        history[faculty_id].append((meeting_days, time_slot))

    query = select(Faculty).where(Faculty.faculty_id.in_(history)).order_by(
        Faculty.full_name, Faculty.faculty_id
    )
    if apply:
        query = query.with_for_update()
    codec = AvailabilityMaskCodec()
    plan = []
    for faculty in db.scalars(query):
        defaults = infer_defaults(history[faculty.faculty_id], slots)
        fill_mask = needs_mask(faculty.availability_mask) and bool(defaults.slot_ids)
        inferred_days = encode_preferred_days(defaults.preferred_days)
        fill_days = (
            bool(inferred_days)
            and (replace_days or faculty.preferred_days == "")
            and faculty.preferred_days != inferred_days
        )
        if not (fill_mask or fill_days):
            continue
        plan.append(
            FacultyFill(
                faculty.faculty_id,
                faculty.full_name,
                defaults,
                fill_mask,
                fill_days,
                faculty.preferred_days,
            )
        )
        if apply and fill_mask:
            faculty.availability_mask = codec.encode(defaults.slot_ids, slots)
        if apply and fill_days:
            faculty.preferred_days = inferred_days
    return plan
