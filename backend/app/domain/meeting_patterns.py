"""Which day patterns a class may meet on, by term, and how long each meeting runs.

DCISM runs two semesters and a summer term:

* Terms 1 and 2 meet twice a week, Monday-Wednesday (``MW``) or
  Tuesday-Thursday (``TTh``), or once on Friday (``F``). A Friday-only class
  holds both weekly sessions back to back, so its meeting runs twice the
  subject's session length: 3h for a lecture, 5h for a lab.
* Term 3 is the summer term. It packs the load into a short term, so every
  class meets Monday to Thursday (``MTWTh``) at the normal session length. The
  only other pattern a class may be given there, by hand, is Friday-only
  (``F``), held back to back as in a semester.

* In a semester a class may also meet Friday-Saturday (``FSa``) or on a single
  weekend day (``Sa`` or ``Su``). The class editor sets these by hand; in a
  semester the GA draws one only for a subject that prefers it. Sunday is not
  a class day: it is only a placeholder for the practicum (OJT) schedule.
* Saturday-only (``Sa``) is held like Friday-only: both weekly sessions back
  to back, 3h for a lecture and 5h for a lab. Friday-Saturday (``FSa``) splits
  the week across the two days, one normal-length session each day (1h30 for
  a lecture, 2h30 for a lab).

A Friday-only class keeps the normal-length time slot it starts in; the
second session is the slot of the same length that begins when the first one
ends. Keeping the term's slot grid unchanged keeps every saved faculty
availability mask valid.
"""

from datetime import datetime, time, timedelta

from app.domain.calendar import Weekday
from app.domain.scheduling import TimeSlotDTO

SUMMER_TERM_NUMBER = 3

SEMESTER_MEETING_PATTERNS: tuple[str, ...] = ("MW", "TTh", "F")
SUMMER_MEETING_PATTERNS: tuple[str, ...] = ("MTWTh",)
# The one pattern a summer class may be set to by hand besides MTWTh.
SUMMER_MANUAL_PATTERNS: tuple[str, ...] = ("F",)
# Weekend meetings (Su is the practicum/OJT placeholder): accepted on a manual
# edit in every term; the GA draws one only for a subject that prefers it.
WEEKEND_MEETING_PATTERNS: tuple[str, ...] = ("FSa", "Sa", "Su")

# Every pattern a subject may prefer, in display and storage order.
SUBJECT_PREFERENCE_PATTERNS: tuple[str, ...] = ("MW", "TTh", "FSa", "F", "Sa", "Su")

# Patterns that hold two sessions back to back in a single meeting: one day
# carries the whole week's hours. FSa splits them across two days instead.
DOUBLE_SESSION_PATTERNS: frozenset[str] = frozenset({"F", "Sa"})

_ANCHOR = datetime(2000, 1, 3)


# Two-letter tokens first, so "Th" is never read as "T" followed by "h".
_PATTERN_TOKENS: tuple[tuple[str, Weekday], ...] = (
    ("Th", Weekday.THURSDAY),
    ("Sa", Weekday.SATURDAY),
    ("Su", Weekday.SUNDAY),
    ("M", Weekday.MONDAY),
    ("T", Weekday.TUESDAY),
    ("W", Weekday.WEDNESDAY),
    ("F", Weekday.FRIDAY),
)


def pattern_weekdays(pattern: str) -> frozenset[Weekday]:
    """The weekdays a pattern meets on: "MW" -> {MON, WED}, "TTh" -> {TUE, THU}."""
    days: set[Weekday] = set()
    offset = 0
    while offset < len(pattern):
        match = next(
            ((token, day) for token, day in _PATTERN_TOKENS if pattern.startswith(token, offset)),
            None,
        )
        if match is None:
            raise ValueError(f"Invalid meeting_days pattern: {pattern}")
        days.add(match[1])
        offset += len(match[0])
    return frozenset(days)


def is_summer_term(term_number: int) -> bool:
    return term_number == SUMMER_TERM_NUMBER


def generated_meeting_patterns(term_number: int) -> tuple[str, ...]:
    """The patterns the GA may place a class on in this term."""
    if is_summer_term(term_number):
        return SUMMER_MEETING_PATTERNS
    return SEMESTER_MEETING_PATTERNS


def allowed_meeting_patterns(term_number: int) -> tuple[str, ...]:
    """Every pattern a class in this term may meet on, including those set
    by hand: weekend meetings in a semester, Friday-only in the summer."""
    if is_summer_term(term_number):
        return SUMMER_MEETING_PATTERNS + SUMMER_MANUAL_PATTERNS
    return generated_meeting_patterns(term_number) + WEEKEND_MEETING_PATTERNS


def term_meeting_patterns(
    term_number: int, configured: tuple[str, ...]
) -> tuple[str, ...]:
    """The patterns a GA run draws from in this term.

    The summer term has exactly one pattern, so it ignores the configuration.
    A semester uses the configured patterns it allows, in configured order.
    """
    if is_summer_term(term_number):
        return SUMMER_MEETING_PATTERNS
    allowed = generated_meeting_patterns(term_number)
    return tuple(pattern for pattern in configured if pattern in allowed)


def subject_meeting_patterns(
    term_number: int, term_patterns: tuple[str, ...], preferred: tuple[str, ...]
) -> tuple[str, ...]:
    """The patterns a GA run may place one subject on.

    The term's patterns, plus any weekend pattern the subject prefers. The
    summer term packs every class into MTWTh, so it never adds one.
    """
    if is_summer_term(term_number):
        return term_patterns
    extra = tuple(p for p in WEEKEND_MEETING_PATTERNS if p in preferred)
    return term_patterns + extra


def sessions_per_meeting(pattern: str | None) -> int:
    return 2 if pattern in DOUBLE_SESSION_PATTERNS else 1


def stretched_end(start: time, end: time, pattern: str | None) -> time:
    """The end of a meeting whose first session runs ``start``-``end``."""
    sessions = sessions_per_meeting(pattern)
    if sessions == 1:
        return end
    begin = datetime.combine(_ANCHOR, start)
    finish = begin + (datetime.combine(_ANCHOR, end) - begin) * sessions
    if finish.date() != begin.date():
        return time.max
    return finish.time()


def meeting_end(slot: TimeSlotDTO, pattern: str | None) -> time:
    """When a meeting that starts in ``slot`` on ``pattern`` ends."""
    return stretched_end(slot.start_time, slot.end_time, pattern)


def meeting_label(slot: TimeSlotDTO, pattern: str | None) -> str:
    """The slot's label with the whole meeting's window, e.g. "LEC 07:30-10:30"."""
    end = meeting_end(slot, pattern)
    if end == slot.end_time:
        return slot.label
    kind = slot.label.split(" ", 1)[0] if " " in slot.label else ""
    window = f"{slot.start_time:%H:%M}-{end:%H:%M}"
    return f"{kind} {window}" if kind else window


def meeting_slot(slot: TimeSlotDTO, pattern: str | None) -> TimeSlotDTO:
    """``slot`` stretched to the whole meeting; the same slot for one session."""
    end = meeting_end(slot, pattern)
    if end == slot.end_time:
        return slot
    return TimeSlotDTO(
        time_slot_id=slot.time_slot_id,
        term_id=slot.term_id,
        label=slot.label,
        start_time=slot.start_time,
        end_time=end,
        ordinal=slot.ordinal,
    )


def continuation_slots(
    slot: TimeSlotDTO,
    pattern: str | None,
    time_slots: "list[TimeSlotDTO] | tuple[TimeSlotDTO, ...]",
) -> tuple[TimeSlotDTO, ...] | None:
    """The slots holding a meeting's later sessions, or None past closing.

    Each later session is the slot of the same length that starts when the
    previous session ends. A one-session pattern has none.
    """
    sessions = sessions_per_meeting(pattern)
    if sessions == 1:
        return ()
    by_start = {
        (candidate.start_time, candidate.duration): candidate
        for candidate in time_slots
    }
    chain: list[TimeSlotDTO] = []
    current = slot
    for _ in range(sessions - 1):
        following = by_start.get((current.end_time, slot.duration))
        if following is None:
            return None
        chain.append(following)
        current = following
    return tuple(chain)


def meeting_duration(slot: TimeSlotDTO, pattern: str | None) -> timedelta:
    return slot.duration * sessions_per_meeting(pattern)


# --- Subject day preferences -------------------------------------------------
#
# A subject may prefer some patterns, e.g. a lab that is usually taught
# Friday-only, or NSTP on Saturday. The preference is soft: the GA draws a
# preferred pattern first and falls back to the term's others when none fits.
# Stored comma-separated in SUBJECT_PREFERENCE_PATTERNS order ("MW,F"); empty
# means no preference.


def normalize_preferred_patterns(patterns) -> tuple[str, ...]:
    """Deduplicate and order a subject's preferred patterns; refuse others."""
    chosen = set(patterns)
    unknown = sorted(chosen - set(SUBJECT_PREFERENCE_PATTERNS))
    if unknown:
        raise ValueError(
            "preferred day patterns may only be MW, TTh, FSa, F, Sa or Su, "
            f"not {', '.join(unknown)}"
        )
    return tuple(pattern for pattern in SUBJECT_PREFERENCE_PATTERNS if pattern in chosen)


def encode_preferred_patterns(patterns) -> str:
    return ",".join(normalize_preferred_patterns(patterns))


def decode_preferred_patterns(stored: str | None) -> tuple[str, ...]:
    """Read a stored preference; unknown values are skipped, never raised."""
    if not stored:
        return ()
    values = set(stored.split(","))
    return tuple(pattern for pattern in SUBJECT_PREFERENCE_PATTERNS if pattern in values)


def first_choice_patterns(
    term_patterns: tuple[str, ...], preferred: tuple[str, ...]
) -> tuple[str, ...]:
    """The patterns the GA tries first for a subject, in ``term_patterns`` order.

    A subject's preferred patterns come first when the term allows any of
    them. Without one, the subject sticks to the regular two-day patterns, so
    Friday-only goes to the subjects that ask for it rather than at random.
    The term's remaining patterns stay available as a fallback.
    """
    chosen = tuple(pattern for pattern in term_patterns if pattern in preferred)
    if chosen:
        return chosen
    regular = tuple(
        pattern for pattern in term_patterns if sessions_per_meeting(pattern) == 1
    )
    return regular or term_patterns
