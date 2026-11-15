from uuid import UUID, uuid4

import pytest

from app.core.errors import APIError
from app.models import Term
from app.services.catalog.guards import (
    count_where,
    ensure_not_referenced,
    ensure_reference_exists,
    ensure_unique,
    get_active_term,
    require_active_term,
)

ACTIVE_TERM_ID = UUID("00000000-0000-0000-0000-0000000000a1")


class FakeTerm:
    def __init__(self, term_id: UUID):
        self.term_id = term_id
        self.is_active = True


class FakeSession:
    """Stands in for a Session; records what it was asked and returns canned values."""

    def __init__(self, scalar_results=None, get_result=None):
        self._scalar_results = list(scalar_results or [])
        self._get_result = get_result

    def scalar(self, statement):
        if not self._scalar_results:
            return None
        return self._scalar_results.pop(0)

    def get(self, model, identity):
        return self._get_result


def test_get_active_term_returns_the_active_row():
    term = FakeTerm(ACTIVE_TERM_ID)

    assert get_active_term(FakeSession([term])) is term


def test_get_active_term_raises_when_no_term_is_active():
    with pytest.raises(APIError) as raised:
        get_active_term(FakeSession([None]))

    assert (raised.value.status_code, raised.value.code) == (409, "NO_ACTIVE_TERM")


def test_require_active_term_accepts_the_active_term():
    term = FakeTerm(ACTIVE_TERM_ID)

    assert require_active_term(FakeSession([term]), ACTIVE_TERM_ID) is term


def test_require_active_term_rejects_a_different_term():
    other_id = uuid4()

    with pytest.raises(APIError) as raised:
        require_active_term(FakeSession([FakeTerm(ACTIVE_TERM_ID)]), other_id)

    assert (raised.value.status_code, raised.value.code) == (409, "TERM_NOT_ACTIVE")
    assert raised.value.details == {
        "active_term_id": str(ACTIVE_TERM_ID),
        "requested_term_id": str(other_id),
    }


# ensure_unique and ensure_reference_exists build real column expressions, so
# they are exercised against the mapped Term; the fake session never executes
# the statement they produce.
def test_ensure_unique_passes_when_nothing_matches():
    ensure_unique(FakeSession([0]), Term, academic_year="2025-2026", term_number=1)


def test_ensure_unique_reports_the_conflicting_keys():
    with pytest.raises(APIError) as raised:
        ensure_unique(FakeSession([1]), Term, academic_year="2025-2026", term_number=1)

    assert (raised.value.status_code, raised.value.code) == (409, "DUPLICATE_RESOURCE")
    assert raised.value.details == {
        "conflict": {"academic_year": "2025-2026", "term_number": "1"}
    }


def test_ensure_reference_exists_reports_the_offending_field():
    with pytest.raises(APIError) as raised:
        ensure_reference_exists(
            FakeSession(get_result=None), Term, "CS101", "subject_code"
        )

    assert (raised.value.status_code, raised.value.code) == (409, "UNKNOWN_REFERENCE")
    assert raised.value.details == {"field": "subject_code", "value": "CS101"}


def test_ensure_not_referenced_names_every_blocking_dependent():
    session = FakeSession([2, 0, 3])

    with pytest.raises(APIError) as raised:
        ensure_not_referenced(
            session,
            [
                ("course_sections", count_where(Term)),
                ("time_slots", count_where(Term)),
                ("widgets", count_where(Term)),
            ],
        )

    assert (raised.value.status_code, raised.value.code) == (
        409,
        "DEPENDENT_RECORDS_EXIST",
    )
    assert raised.value.details == {
        "dependents": {"course_sections": 2, "widgets": 3},
        # Labels exist only for groups the guard knows how to name.
        "dependent_records": {
            "course_sections": {"total": 2, "items": []},
            "widgets": {"total": 3, "items": []},
        },
    }
