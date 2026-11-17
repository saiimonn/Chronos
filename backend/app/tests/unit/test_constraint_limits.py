"""Hard-constraint limits: validation, Block day retries, and job payloads."""

from dataclasses import replace

import pytest

from app.domain import ConstraintLimits
from app.schemas.hard_constraints import ConstraintLimitsModel
from app.services.jobs.codecs import decode_scheduling_problem, encode_scheduling_problem
from app.services.jobs.handlers import block_day_span_retries
from app.tests.support.scheduling import make_scheduling_problem


@pytest.mark.parametrize(
    "fields",
    [
        {"block_day_target_hours": 11},  # past the 10-hour limit
        {"part_time_max_units": 30},  # above the full-time cap
        {"back_to_back_limit_hours": 12},  # longer than the longest day
        {"back_to_back_limit_hours": 0},
    ],
)
def test_inconsistent_limits_are_refused(fields):
    with pytest.raises(ValueError):
        ConstraintLimits(**fields)
    with pytest.raises(ValueError):
        ConstraintLimitsModel(**fields)


def test_block_day_retries_run_from_target_to_limit_an_hour_at_a_time():
    assert block_day_span_retries(ConstraintLimits()) == (480, 540, 600)
    limits = ConstraintLimits(block_day_target_hours=7, block_day_limit_hours=7)
    assert block_day_span_retries(limits) == (420,)


def test_limits_travel_with_the_job_payload():
    limits = ConstraintLimits(back_to_back_limit_hours=5, full_time_max_units=21)
    payload = encode_scheduling_problem(replace(make_scheduling_problem(), limits=limits))
    assert decode_scheduling_problem(payload).limits == limits

    # A payload queued before limits were configurable uses the standing rules.
    del payload["limits"]
    assert decode_scheduling_problem(payload).limits == ConstraintLimits()


def test_versions_saved_with_the_old_per_type_counts_still_load():
    old = {
        "full_time_max_units": 22,
        "max_consecutive_lectures": 3,
        "max_consecutive_labs": 2,
        "block_run_limit_hours": 5,
    }
    loaded = ConstraintLimits.from_saved(old)
    assert loaded.full_time_max_units == 22
    assert loaded.back_to_back_limit_hours == 5
