"""CHR-85 / CHR-RBAC-007 guards on the selected-history ARM contract.

The selection is checked before anything is enqueued, so a bad selection is an
answer to the administrator rather than a job that fails hours later.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.jobs import SubmitARMJobRequest
from app.services.arm.preprocessing import (
    HistoricalInclusionPolicy,
    HistoricalOffering,
    HistoricalTerm,
)
from app.services.job_submission import (
    HistoricalSelectionError,
    _reject_ineligible_selection,
    _reject_unknown_snapshots,
    _reject_unparsable_academic_years,
    _reject_untrainable_selection,
)

THRESHOLDS = {
    stream: {
        "min_support": "0.1",
        "min_confidence": "0.5",
        "min_lift": "1.0",
        "min_term_recurrence": 1,
    }
    for stream in (
        "teacher_subject",
        "teacher_day",
        "teacher_time",
        "teacher_subject_room",
    )
}


def _term(academic_year: str, term_number: int) -> HistoricalTerm:
    return HistoricalTerm(
        snapshot_id=uuid4(),
        academic_year=academic_year,
        term_number=term_number,
        offerings=(
            HistoricalOffering(
                historical_record_id=uuid4(),
                faculty_id=uuid4(),
                subject_code="ARM101",
                meeting_days="MWF",
                time_slot="07:30-08:30",
                room_code="ARMR1",
            ),
        ),
    )


def _terms(count: int) -> tuple[HistoricalTerm, ...]:
    return tuple(
        _term(f"{2020 + index}-{2021 + index}", 1) for index in range(count)
    )


def _empty_terms(count: int) -> tuple[HistoricalTerm, ...]:
    return tuple(
        HistoricalTerm(
            snapshot_id=uuid4(),
            academic_year=f"{2020 + index}-{2021 + index}",
            term_number=1,
            offerings=(),
        )
        for index in range(count)
    )


def test_request_rejects_duplicate_snapshot_ids():
    snapshot_id = str(uuid4())

    with pytest.raises(ValidationError, match="unique"):
        SubmitARMJobRequest(
            selected_snapshot_ids=[snapshot_id, snapshot_id],
            thresholds=THRESHOLDS,
        )


def test_request_rejects_an_empty_selection():
    with pytest.raises(ValidationError):
        SubmitARMJobRequest(selected_snapshot_ids=[], thresholds=THRESHOLDS)


def test_unknown_snapshot_ids_are_reported_rather_than_dropped():
    loaded = _terms(1)
    unknown = UUID("00000000-0000-0000-0000-0000000000ff")

    with pytest.raises(HistoricalSelectionError) as error:
        _reject_unknown_snapshots(
            [loaded[0].snapshot_id, unknown], loaded
        )

    assert error.value.snapshot_ids == (unknown,)


def test_a_selection_that_exists_passes_the_existence_guard():
    loaded = _terms(2)

    _reject_unknown_snapshots([term.snapshot_id for term in loaded], loaded)


def test_a_selection_no_larger_than_the_holdout_is_accepted():
    """Operational mining does not withhold the holdout (thesis §4.3), so even a
    selection that evaluation would reserve entirely can be mined."""
    _reject_untrainable_selection(_terms(1))
    _reject_untrainable_selection(_terms(HistoricalInclusionPolicy.HOLDOUT_TERM_COUNT))


def test_training_terms_without_historical_records_are_rejected():
    terms = _empty_terms(HistoricalInclusionPolicy.HOLDOUT_TERM_COUNT + 1)

    with pytest.raises(
        HistoricalSelectionError, match="no mineable historical records"
    ):
        _reject_untrainable_selection(terms)


def test_a_malformed_academic_year_is_reported_as_a_selection_problem():
    """The listing offers such a term as eligible for nothing, so it can still be
    selected. Partitioning it raises a bare ValueError, which would otherwise
    escape the request as a 500."""
    malformed = _term("2024/2025", 1)
    terms = (*_terms(HistoricalInclusionPolicy.HOLDOUT_TERM_COUNT + 1), malformed)

    with pytest.raises(HistoricalSelectionError, match="malformed academic year"):
        _reject_unparsable_academic_years(terms)

    with pytest.raises(HistoricalSelectionError) as error:
        _reject_unparsable_academic_years(terms)
    assert error.value.snapshot_ids == (malformed.snapshot_id,)


def test_well_formed_academic_years_pass_the_parse_guard():
    _reject_unparsable_academic_years(_terms(2))


def test_notes_are_bounded_by_the_column_that_stores_them():
    """`RuleSet.notes` is VARCHAR(500); a longer note would be accepted here and
    then fail when the worker persists the draft."""
    snapshot_id = str(uuid4())

    accepted = SubmitARMJobRequest(
        selected_snapshot_ids=[snapshot_id],
        thresholds=THRESHOLDS,
        notes="n" * 500,
    )
    assert accepted.notes is not None and len(accepted.notes) == 500

    with pytest.raises(ValidationError):
        SubmitARMJobRequest(
            selected_snapshot_ids=[snapshot_id],
            thresholds=THRESHOLDS,
            notes="n" * 501,
        )


def test_a_term_older_than_every_stream_window_is_refused():
    """`2010-2011` predates the earliest year of all four streams, so every
    partition drops it. Mixed with eligible terms the run would still be
    enqueued and would silently mine less history than was selected."""
    too_old = _term("2010-2011", 1)
    terms = (*_terms(HistoricalInclusionPolicy.HOLDOUT_TERM_COUNT + 1), too_old)

    with pytest.raises(HistoricalSelectionError) as error:
        _reject_ineligible_selection(terms)

    assert error.value.snapshot_ids == (too_old.snapshot_id,)
    assert "older than every pattern stream" in error.value.message


def test_a_term_eligible_for_one_stream_passes_the_eligibility_guard():
    """`2018-2019` is outside the three newer streams' windows but inside
    `TEACHER_SUBJECT`'s, so it does contribute to the run."""
    _reject_ineligible_selection((_term("2018-2019", 1), *_terms(2)))
