"""CHR-171 / CHR-FIX-011 exact, persistable ARM request thresholds."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.jobs import ARMStreamThresholdsRequest


@pytest.mark.parametrize(
    "field,value",
    [
        (field, value)
        for field in ("min_support", "min_confidence", "min_lift")
        for value in ("0.00001", "0.123456", "1e-5", "0.12340000000000000000000000000000001")
    ] + [
        ("min_lift", "10000"),
        ("min_lift", "1e4"),
        ("min_lift", "9999.99990000000000000000000000000001"),
    ],
)
def test_threshold_request_rejects_values_the_database_cannot_store_exactly(
    field, value,
):
    values = dict(
        min_support="0.1", min_confidence="0.5", min_lift="0", min_term_recurrence=1,
    )
    values[field] = value

    with pytest.raises(ValidationError) as error:
        ARMStreamThresholdsRequest.model_validate(values)

    assert [item["loc"] for item in error.value.errors()] == [(field,)]


@pytest.mark.parametrize("value", [
    "0.0001", "0.1234", "0.123400", "1.00000", "1e-4", 0.1234,
    "0.12340000000000000000000000000000000",
])
@pytest.mark.parametrize("field", ["min_support", "min_confidence", "min_lift"])
def test_threshold_request_preserves_representable_values(field, value):
    values = dict(
        min_support="0.1", min_confidence="0.5", min_lift="0", min_term_recurrence=1,
    )
    values[field] = value

    request = ARMStreamThresholdsRequest.model_validate(values)

    assert getattr(request, field) == Decimal(str(value))


@pytest.mark.parametrize("value", [
    "0", "0.00000", "0e-8", "9999.9999", "9999.999900", "9.9999999e3",
    "9999.99990000000000000000000000000000",
])
def test_threshold_request_accepts_the_full_lift_column_range(value):
    request = ARMStreamThresholdsRequest(
        min_support="0.1", min_confidence="0.5", min_lift=value, min_term_recurrence=1,
    )

    assert request.min_lift == Decimal(value)


@pytest.mark.parametrize("value", [
    "0e2147483648", "0e-2147483648", "-0e2147483648", "-0e-2147483648",
])
def test_threshold_request_canonicalizes_zero_for_postgres(value):
    request = ARMStreamThresholdsRequest(
        min_support="0.1", min_confidence="0.5", min_lift=value, min_term_recurrence=1,
    )

    assert request.min_lift.as_tuple() == Decimal(0).as_tuple()
