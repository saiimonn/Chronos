from datetime import time

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Term, TimeSlot

pytestmark = pytest.mark.postgres


def make_term(session: Session, **overrides) -> Term:
    values = {"academic_year": "2025-2026", "term_number": 1, "is_active": True}
    values.update(overrides)
    term = Term(**values)
    session.add(term)
    session.flush()
    return term


def test_term_rejects_duplicate_identity(db_session: Session):
    make_term(db_session)
    db_session.add(Term(academic_year="2025-2026", term_number=1, is_active=False))
    with pytest.raises(IntegrityError, match="uq_term_academic_year_term_number"):
        db_session.flush()


def test_only_one_term_may_be_active(db_session: Session):
    make_term(db_session)
    db_session.add(Term(academic_year="2026-2027", term_number=1, is_active=True))
    with pytest.raises(IntegrityError, match="uq_term_single_active"):
        db_session.flush()


def test_time_slot_is_term_scoped_and_ordered(db_session: Session):
    term = make_term(db_session)
    slot = TimeSlot(term_id=term.term_id, label="08:00-09:00", start_time=time(8), end_time=time(9), ordinal=0)
    db_session.add(slot)
    db_session.flush()
    assert slot.time_slot_id is not None

    db_session.add(TimeSlot(term_id=term.term_id, label="09:00-10:00", start_time=time(9), end_time=time(10), ordinal=0))
    with pytest.raises(IntegrityError, match="uq_time_slot_term_ordinal"):
        db_session.flush()


def test_time_slot_rejects_invalid_range(db_session: Session):
    term = make_term(db_session)
    db_session.add(TimeSlot(term_id=term.term_id, label="invalid", start_time=time(10), end_time=time(10), ordinal=0))
    with pytest.raises(IntegrityError, match="ck_time_slot_time_range"):
        db_session.flush()
