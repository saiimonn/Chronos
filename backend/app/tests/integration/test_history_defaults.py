import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import Faculty, HistoricalRecord, HistoricalSnapshot
from app.services.catalog.availability import _active_grid
from app.services.catalog.history_defaults import fill_from_history
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def test_blank_faculty_are_filled_and_saved_ones_kept(db_session: Session):
    client = make_api_client(db_session)
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post(f"/api/v1/terms/{term['term_id']}/select")
    blank = client.post(
        "/api/v1/faculty", json={"full_name": "Blank", "is_full_time": False}
    ).json()["faculty_id"]
    saved = client.post(
        "/api/v1/faculty",
        json={"full_name": "Saved", "is_full_time": False, "preferred_days": ["FRI"]},
    ).json()["faculty_id"]
    no_history = client.post(
        "/api/v1/faculty", json={"full_name": "New", "is_full_time": False}
    ).json()["faculty_id"]
    _, slots = _active_grid(db_session)
    saved_mask = "1" + "0" * (len(slots) - 1)
    db_session.get(Faculty, uuid.UUID(saved)).availability_mask = saved_mask

    snapshot = HistoricalSnapshot(
        source_term_id=uuid.uuid4(), academic_year="2024-2025", term_number=1
    )
    db_session.add(snapshot)
    db_session.flush()
    for faculty_id in (blank, saved):
        for days in ("TTh", "TTh", "TTh", "MW"):
            db_session.add(
                HistoricalRecord(
                    snapshot_id=snapshot.snapshot_id,
                    source_faculty_id=uuid.UUID(faculty_id),
                    faculty_name="X",
                    subject_code="CS 1",
                    subject_title="Intro",
                    meeting_days=days,
                    time_slot="13:00-16:00",
                )
            )
    db_session.commit()

    plan = fill_from_history(db_session, slots, apply=True)
    db_session.commit()

    assert [item.full_name for item in plan] == ["Blank"]
    filled = db_session.get(Faculty, uuid.UUID(blank))
    assert filled.preferred_days == "TUE,THU"
    chosen = {
        slot.label for slot, bit in zip(slots, filled.availability_mask) if bit == "1"
    }
    assert "LEC 13:00-14:30" in chosen and "LAB 13:00-15:30" in chosen
    assert "LEC 07:30-09:00" not in chosen and "LAB 13:30-16:00" in chosen
    assert "LAB 14:00-16:30" not in chosen
    kept = db_session.get(Faculty, uuid.UUID(saved))
    assert (kept.preferred_days, kept.availability_mask) == ("FRI", saved_mask)
    assert db_session.get(Faculty, uuid.UUID(no_history)).availability_mask == ""

    # replace_days recomputes saved days too, and still keeps a saved mask.
    replaced = fill_from_history(db_session, slots, apply=True, replace_days=True)
    db_session.commit()

    assert [(item.full_name, item.previous_days) for item in replaced] == [
        ("Saved", "FRI")
    ]
    kept = db_session.get(Faculty, uuid.UUID(saved))
    db_session.refresh(kept)
    assert (kept.preferred_days, kept.availability_mask) == ("TUE,THU", saved_mask)
    # Nothing left to change: a second run plans nothing.
    assert fill_from_history(db_session, slots, apply=False, replace_days=True) == []
