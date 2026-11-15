"""Per-day faculty availability: a day (say, Monday) with its own hours."""

from dataclasses import replace
from random import Random

import pytest

from app.domain import ConflictCode, Weekday
from app.domain.meeting_patterns import pattern_weekdays
from app.services.ga.operators import GeneSampler
from app.services.jobs.codecs import (
    decode_scheduling_problem,
    encode_scheduling_problem,
)
from app.services.readiness import analyze_readiness
from app.services.validation import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    decode_day_masks,
    encode_day_masks,
)
from app.tests.unit.test_meeting_patterns import (
    CLASS_ID,
    FACULTY_ID,
    OTHER_FACULTY_ID,
    SLOT_0730,
    SLOT_0900,
    SLOT_1930,
    SLOTS,
    _assign,
    _candidate,
    _codes,
    _config,
    _problem,
)


def _with_days(problem, day_masks: str, mask: str = "111"):
    teacher, *rest = problem.faculty
    return replace(
        problem,
        faculty=(
            replace(teacher, availability_mask=mask, day_availability_masks=day_masks),
            *rest,
        ),
    )


# --- Storage ----------------------------------------------------------------


def test_day_masks_round_trip_in_calendar_order():
    stored = encode_day_masks({Weekday.FRIDAY: "100", Weekday.MONDAY: "010"})
    assert stored == "MON:010;FRI:100"
    assert decode_day_masks(stored) == {Weekday.MONDAY: "010", Weekday.FRIDAY: "100"}
    assert decode_day_masks("") == {}


@pytest.mark.parametrize("stored", ("MON", "XYZ:101", "MON:1;MON:0"))
def test_malformed_day_masks_raise(stored):
    with pytest.raises(AvailabilityMaskError):
        decode_day_masks(stored)


def test_weekly_availability_intersects_the_days_a_class_meets():
    weekly = AvailabilityMaskCodec().weekly("111", "MON:100", SLOTS)
    assert weekly.on(Weekday.TUESDAY) == {SLOT_0730, SLOT_0900, SLOT_1930}
    assert weekly.on(Weekday.MONDAY) == {SLOT_0730}
    assert weekly.on_days({Weekday.MONDAY, Weekday.WEDNESDAY}) == {SLOT_0730}
    assert weekly.any_day() == {SLOT_0730, SLOT_0900, SLOT_1930}


def test_an_override_must_fit_the_grid():
    with pytest.raises(AvailabilityMaskError):
        AvailabilityMaskCodec().weekly("111", "MON:10", SLOTS)


def test_pattern_weekdays_reads_two_letter_tokens():
    assert pattern_weekdays("TTh") == {Weekday.TUESDAY, Weekday.THURSDAY}
    assert pattern_weekdays("MTWTh") == {
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
    }
    assert pattern_weekdays("FSa") == {Weekday.FRIDAY, Weekday.SATURDAY}


# --- Hard constraint --------------------------------------------------------


def test_a_shorter_monday_blocks_mw_but_not_tth():
    # Free all day, except Mondays only until 09:00.
    problem = _with_days(_problem(1), "MON:100")
    monday = _candidate(_assign(CLASS_ID, "MW", SLOT_0900))
    tuesday = _candidate(_assign(CLASS_ID, "TTh", SLOT_0900))
    assert ConflictCode.FACULTY_UNAVAILABLE in _codes(problem, monday)
    assert ConflictCode.FACULTY_UNAVAILABLE not in _codes(problem, tuesday)
    early = _candidate(_assign(CLASS_ID, "MW", SLOT_0730))
    assert ConflictCode.FACULTY_UNAVAILABLE not in _codes(problem, early)


def test_a_day_can_open_hours_the_default_closes():
    # Closed by default, but free on Fridays at 07:30 and 09:00.
    problem = _with_days(_problem(1), "FRI:110", mask="000")
    friday = _candidate(_assign(CLASS_ID, "F", SLOT_0730))
    assert ConflictCode.FACULTY_UNAVAILABLE not in _codes(problem, friday)
    weekday = _candidate(_assign(CLASS_ID, "MW", SLOT_0730))
    assert ConflictCode.FACULTY_UNAVAILABLE in _codes(problem, weekday)


# --- GA ---------------------------------------------------------------------


def test_the_ga_draws_mw_only_where_monday_is_free():
    problem = _with_days(_problem(1), "MON:100")
    sampler = GeneSampler(problem, _config(("MW", "TTh")))
    offering_id = sampler.offering_ids[0]
    assert sampler.time_slot_pool(offering_id, FACULTY_ID, "MW") == (SLOT_0730,)
    assert sampler.time_slot_pool(offering_id, FACULTY_ID, "TTh") == (
        SLOT_0730,
        SLOT_0900,
        SLOT_1930,
    )
    rng = Random(5)
    for _ in range(50):
        gene = sampler.sample_gene(offering_id, rng)
        if gene.faculty_id == FACULTY_ID and gene.meeting_days == "MW":
            assert gene.time_slot_id == SLOT_0730


def test_the_ga_skips_a_teacher_whose_days_fit_no_pattern():
    # Summer meets MTWTh; a teacher closed on Mondays can never take it.
    problem = _with_days(_problem(3), "MON:000")
    sampler = GeneSampler(problem, _config(("MW", "TTh")))
    rng = Random(2)
    for offering_id in sampler.offering_ids:
        for _ in range(20):
            gene = sampler.sample_gene(offering_id, rng)
            assert gene.faculty_id == OTHER_FACULTY_ID


def test_without_overrides_the_ga_draws_exactly_as_before():
    plain = GeneSampler(_problem(1), _config(("MW", "TTh")))
    same = GeneSampler(_with_days(_problem(1), ""), _config(("MW", "TTh")))
    assert plain.sample_individual(Random(9)) == same.sample_individual(Random(9))


def test_job_payload_keeps_day_masks():
    problem = _with_days(_problem(1), "MON:100")
    decoded = decode_scheduling_problem(encode_scheduling_problem(problem))
    assert decoded.faculty[0].day_availability_masks == "MON:100"
    plain = encode_scheduling_problem(_problem(1))
    assert "day_availability_masks" not in plain["faculty"][0]


# --- Readiness --------------------------------------------------------------


def test_readiness_flags_an_override_saved_for_another_grid():
    report = analyze_readiness(_with_days(_problem(1), "MON:10"))
    assert "FACULTY_MASK_LENGTH_MISMATCH" in {b.code for b in report.blockers}
