"""CHR-110 / CHR-ING-002 database-free completed-term approval contracts."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.historical_capture import (
    HistoricalCaptureRequest,
    HistoricalCaptureResponse,
)
from app.tests.support.scheduling import SCHEDULE_ID, TERM_ID


def test_capture_request_accepts_only_explicit_approval():
    payload = {
        "schedule_id": str(SCHEDULE_ID),
        "confirm_completed_and_approved": True,
    }

    request = HistoricalCaptureRequest.model_validate(payload)

    assert request.schedule_id == SCHEDULE_ID
    assert request.confirm_completed_and_approved is True
    assert request.model_dump(mode="json") == payload


@pytest.mark.parametrize("confirmation", [False, "true", "false", 1, 0, None])
def test_capture_request_does_not_coerce_confirmation(confirmation):
    with pytest.raises(ValidationError) as error:
        HistoricalCaptureRequest.model_validate(
            {
                "schedule_id": str(SCHEDULE_ID),
                "confirm_completed_and_approved": confirmation,
            }
        )

    assert [item["loc"] for item in error.value.errors()] == [
        ("confirm_completed_and_approved",)
    ]


@pytest.mark.parametrize(
    ("payload", "field", "error_type"),
    [
        ({"confirm_completed_and_approved": True}, "schedule_id", "missing"),
        (
            {"schedule_id": str(SCHEDULE_ID)},
            "confirm_completed_and_approved",
            "missing",
        ),
        (
            {"schedule_id": "not-a-uuid", "confirm_completed_and_approved": True},
            "schedule_id",
            "uuid_parsing",
        ),
        (
            {
                "schedule_id": str(SCHEDULE_ID),
                "confirm_completed_and_approved": True,
                "approved_by_user_id": str(TERM_ID),
            },
            "approved_by_user_id",
            "extra_forbidden",
        ),
    ],
)
def test_capture_request_rejects_missing_malformed_or_extra_fields(
    payload, field, error_type
):
    with pytest.raises(ValidationError) as error:
        HistoricalCaptureRequest.model_validate(payload)

    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        ((field,), error_type)
    ]


def test_bootstrap_response_preserves_null_capture_provenance():
    payload = {
        "snapshot_id": "00000000-0000-0000-0000-000000000020",
        "source_term_id": str(TERM_ID),
        "source_schedule_id": None,
        "approved_by_user_id": None,
        "academic_year": "2023-2024",
        "term_number": 1,
        "captured_at": "2024-05-15T12:00:00Z",
        "record_count": 1,
    }

    response = HistoricalCaptureResponse.model_validate(payload)

    assert response.snapshot_id == UUID(payload["snapshot_id"])
    assert response.source_term_id == TERM_ID
    assert response.captured_at == datetime(2024, 5, 15, 12, tzinfo=UTC)
    assert response.model_dump(mode="json") == payload
