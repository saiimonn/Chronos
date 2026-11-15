from datetime import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alembic_support.canonical_slots import regrid_terms
from app.models import Faculty, ScheduleRecord, Subject, Term, TimeSlot
from app.services.catalog.time_slots import ensure_canonical_slots
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def make_term(
    db_session: Session, academic_year: str = "2025-2026", term_number: int = 1
) -> Term:
    term = Term(
        academic_year=academic_year, term_number=term_number, is_active=False
    )
    db_session.add(term)
    db_session.flush()
    return term


def test_a_term_is_materialized_with_the_whole_canonical_grid(db_session: Session):
    term = make_term(db_session)

    slots = ensure_canonical_slots(db_session, term.term_id)

    assert len(slots) == 48
    assert [slot.ordinal for slot in slots] == list(range(48))
    assert slots[0].label == "LEC 07:30-09:00"
    assert (slots[0].start_time, slots[0].end_time) == (time(7, 30), time(9, 0))
    assert slots[47].label == "LAB 18:30-21:00"
    assert (slots[47].start_time, slots[47].end_time) == (time(18, 30), time(21, 0))


def test_generating_twice_changes_nothing(db_session: Session):
    term = make_term(db_session)

    first = [s.time_slot_id for s in ensure_canonical_slots(db_session, term.term_id)]
    second = [s.time_slot_id for s in ensure_canonical_slots(db_session, term.term_id)]

    assert first == second
    assert len(second) == 48


def test_two_terms_get_their_own_slot_identities(db_session: Session):
    first_term = make_term(db_session)
    second_term = make_term(db_session, academic_year="2024-2025", term_number=2)

    first = {
        s.time_slot_id for s in ensure_canonical_slots(db_session, first_term.term_id)
    }
    second = {
        s.time_slot_id for s in ensure_canonical_slots(db_session, second_term.term_id)
    }

    assert first.isdisjoint(second)


def test_the_grid_covers_both_subject_durations(db_session: Session):
    term = make_term(db_session)

    slots = ensure_canonical_slots(db_session, term.term_id)

    durations = {
        (
            slot.end_time.hour * 60
            + slot.end_time.minute
            - slot.start_time.hour * 60
            - slot.start_time.minute
        )
        for slot in slots
    }
    assert durations == {90, 150}


def test_creating_a_term_through_the_api_gives_it_the_canonical_grid(
    db_session: Session,
):
    client = make_api_client(db_session)

    term = client.post(
        "/api/v1/terms", json={"academic_year": "2026-2027", "term_number": 1}
    ).json()

    listed = client.get(
        "/api/v1/time-slots", params={"term_id": term["term_id"]}
    ).json()["items"]

    assert len(listed) == 48
    assert [item["ordinal"] for item in listed] == list(range(48))
    assert listed[0]["label"] == "LEC 07:30-09:00"
    assert listed[47]["label"] == "LAB 18:30-21:00"


def test_a_rejected_term_leaves_no_slots_behind(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/terms", json={"academic_year": "2026-2027", "term_number": 2})

    duplicate = client.post(
        "/api/v1/terms", json={"academic_year": "2026-2027", "term_number": 2}
    )

    assert duplicate.status_code == 409
    terms = client.get("/api/v1/terms").json()["items"]
    matching = [
        item
        for item in terms
        if item["academic_year"] == "2026-2027" and item["term_number"] == 2
    ]
    assert len(matching) == 1
    listed = client.get(
        "/api/v1/time-slots", params={"term_id": matching[0]["term_id"]}
    ).json()["items"]
    assert len(listed) == 48


def test_the_regrid_replaces_hand_entered_slots(db_session: Session):
    term = make_term(db_session, academic_year="2023-2024", term_number=1)
    db_session.add(
        TimeSlot(
            term_id=term.term_id,
            label="hand-entered",
            start_time=time(8),
            end_time=time(9),
            ordinal=0,
        )
    )
    db_session.flush()

    regrid_terms(db_session.connection())
    db_session.expire_all()

    slots = list(
        db_session.scalars(
            select(TimeSlot)
            .where(TimeSlot.term_id == term.term_id)
            .order_by(TimeSlot.ordinal)
        )
    )
    assert len(slots) == 48
    assert "hand-entered" not in {slot.label for slot in slots}
    assert slots[0].label == "LEC 07:30-09:00"


def test_the_regrid_resets_every_availability_mask(db_session: Session):
    db_session.add(
        Faculty(full_name="Ada Lovelace", is_full_time=False, availability_mask="101")
    )
    db_session.flush()

    regrid_terms(db_session.connection())
    db_session.expire_all()

    masks = {member.availability_mask for member in db_session.scalars(select(Faculty))}
    assert masks == {"0" * 48}


def test_the_regrid_leaves_historical_records_alone(db_session: Session):
    """A historical row records a past offering as text and carries no provenance.

    It is ARM's input, so re-gridding must not touch it. The placed-record case
    is covered end to end by test_postgresql_migrations, which seeds a real
    assignment and runs the whole chain.
    """
    term = make_term(db_session, academic_year="2021-2022", term_number=1)
    faculty = Faculty(
        full_name="Grace Hopper", is_full_time=True, availability_mask="1"
    )
    db_session.add(faculty)
    db_session.add(Subject(
        subject_code="HIST101",
        subject_title="A past offering",
        units=3,
        subject_type="LEC",
    ))
    db_session.flush()
    db_session.add(
        ScheduleRecord(
            faculty_id=faculty.faculty_id,
            subject_code="HIST101",
            term_id=term.term_id,
            meeting_days="MWF",
            time_slot="08:00-09:00",
            is_historical=True,
        )
    )
    db_session.flush()

    regrid_terms(db_session.connection())
    db_session.expire_all()

    historical = db_session.scalars(
        select(ScheduleRecord).where(ScheduleRecord.is_historical)
    ).all()
    assert [record.meeting_days for record in historical] == ["MWF"]
    assert [record.time_slot for record in historical] == ["08:00-09:00"]
    assert all(record.time_slot_id is None for record in historical)


def test_the_regrid_agrees_with_the_service(db_session: Session):
    """Running the migration then the service must not add a single row."""
    term = make_term(db_session, academic_year="2022-2023", term_number=1)

    regrid_terms(db_session.connection())
    db_session.expire_all()
    after_migration = {
        slot.time_slot_id
        for slot in db_session.scalars(
            select(TimeSlot).where(TimeSlot.term_id == term.term_id)
        )
    }

    after_service = {
        slot.time_slot_id for slot in ensure_canonical_slots(db_session, term.term_id)
    }

    assert after_migration == after_service
    assert len(after_service) == 48
