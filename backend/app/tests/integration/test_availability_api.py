import pytest
from sqlalchemy.orm import Session

from app.models import Faculty
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def prepare(client) -> tuple[str, list[str]]:
    """A term now arrives with its canonical grid, so slots are read, not made."""
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post(f"/api/v1/terms/{term['term_id']}/select")
    slots = client.get(
        "/api/v1/time-slots", params={"term_id": term["term_id"]}
    ).json()["items"]
    slot_ids = [item["time_slot_id"] for item in slots]
    faculty = client.post(
        "/api/v1/faculty", json={"full_name": "Ada Lovelace", "is_full_time": False}
    ).json()
    return faculty["faculty_id"], slot_ids


def mask_for(slot_ids: list[str], chosen: list[str]) -> str:
    """The stored mask a selection produces, built rather than typed."""
    return "".join("1" if slot_id in chosen else "0" for slot_id in slot_ids)


def test_availability_read_and_write_round_trip(db_session: Session):
    client = make_api_client(db_session)
    faculty_id, slot_ids = prepare(client)

    initial = client.get(f"/api/v1/faculty/{faculty_id}/availability")
    assert initial.status_code == 200
    # Never-saved availability defaults to open in every slot.
    assert initial.json()["time_slot_ids"] == slot_ids
    assert [entry["time_slot_id"] for entry in initial.json()["slot_grid"]] == slot_ids

    updated = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": initial.json()["term_id"], "time_slot_ids": [slot_ids[0], slot_ids[2]]},
    )
    assert updated.status_code == 200
    assert updated.json()["time_slot_ids"] == [slot_ids[0], slot_ids[2]]

    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    assert stored.availability_mask == mask_for(slot_ids, [slot_ids[0], slot_ids[2]])
    assert len(stored.availability_mask) == 48

    reread = client.get(f"/api/v1/faculty/{faculty_id}/availability")
    assert reread.json()["time_slot_ids"] == [slot_ids[0], slot_ids[2]]


def test_a_rejected_submission_preserves_the_stored_mask(db_session: Session):
    client = make_api_client(db_session)
    faculty_id, slot_ids = prepare(client)
    term_id = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()["term_id"]
    client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": term_id, "time_slot_ids": [slot_ids[1]]},
    )

    rejected = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": term_id, "time_slot_ids": ["00000000-0000-0000-0000-0000000000ff"]},
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "INVALID_AVAILABILITY_MASK"
    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    assert stored.availability_mask == mask_for(slot_ids, [slot_ids[1]])


def test_preferred_days_save_with_the_mask_and_never_change_it(db_session: Session):
    client = make_api_client(db_session)
    faculty_id, slot_ids = prepare(client)
    initial = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    assert initial["preferred_days"] == []
    term_id = initial["term_id"]

    saved = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={
            "term_id": term_id,
            "time_slot_ids": slot_ids,
            "preferred_days": ["THU", "TUE"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["preferred_days"] == ["TUE", "THU"]
    # Soft data: every slot stays available on every day.
    assert saved.json()["time_slot_ids"] == slot_ids

    # Omitted keeps the stored preference; a rejected save touches neither.
    kept = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": term_id, "time_slot_ids": [slot_ids[0]]},
    )
    assert kept.json()["preferred_days"] == ["TUE", "THU"]
    rejected = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={
            "term_id": term_id,
            "time_slot_ids": ["00000000-0000-0000-0000-0000000000ff"],
            "preferred_days": ["MON", "WED"],
        },
    )
    assert rejected.status_code == 422
    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    assert stored.preferred_days == "TUE,THU"
    assert stored.availability_mask == mask_for(slot_ids, [slot_ids[0]])


def test_availability_requires_an_active_term_with_slots(db_session: Session):
    client = make_api_client(db_session)
    faculty = client.post(
        "/api/v1/faculty", json={"full_name": "Grace Hopper", "is_full_time": True}
    ).json()

    refused = client.get(f"/api/v1/faculty/{faculty['faculty_id']}/availability")

    assert refused.status_code == 409
    assert refused.json()["code"] == "NO_ACTIVE_TERM"


def test_a_day_can_have_its_own_hours(db_session: Session):
    client = make_api_client(db_session)
    faculty_id, slot_ids = prepare(client)
    url = f"/api/v1/faculty/{faculty_id}/availability"
    term_id = client.get(url).json()["term_id"]
    assert client.get(url).json()["day_time_slot_ids"] == {}

    saved = client.put(
        url,
        json={
            "term_id": term_id,
            "time_slot_ids": slot_ids[:3],
            "day_time_slot_ids": {"MON": [slot_ids[0]]},
        },
    )
    assert saved.status_code == 200
    assert saved.json()["day_time_slot_ids"] == {"MON": [slot_ids[0]]}
    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    assert stored.day_availability_masks == "MON:" + mask_for(slot_ids, [slot_ids[0]])

    # Omitting the overrides keeps them; {} clears them.
    kept = client.put(url, json={"term_id": term_id, "time_slot_ids": slot_ids[:2]})
    assert kept.json()["day_time_slot_ids"] == {"MON": [slot_ids[0]]}
    cleared = client.put(
        url,
        json={"term_id": term_id, "time_slot_ids": slot_ids[:2], "day_time_slot_ids": {}},
    )
    assert cleared.json()["day_time_slot_ids"] == {}


def test_a_day_override_outside_the_grid_is_rejected(db_session: Session):
    client = make_api_client(db_session)
    faculty_id, slot_ids = prepare(client)
    url = f"/api/v1/faculty/{faculty_id}/availability"
    term_id = client.get(url).json()["term_id"]
    bogus = "00000000-0000-0000-0000-000000000000"
    rejected = client.put(
        url,
        json={
            "term_id": term_id,
            "time_slot_ids": slot_ids,
            "day_time_slot_ids": {"MON": [bogus]},
        },
    )
    assert rejected.status_code == 422
    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    assert stored.day_availability_masks == ""
