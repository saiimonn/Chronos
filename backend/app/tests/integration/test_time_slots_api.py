import pytest
from sqlalchemy.orm import Session

from app.models import Faculty, UserRole
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

SLOT = {
    # Ordinal 48 and this label sit past the canonical grid, so the manual write
    # path is still exercised without colliding with the generated windows.
    "label": "MANUAL 08:00-09:30",
    "start_time": "08:00:00",
    "end_time": "09:30:00",
    "ordinal": 48,
}


def make_active_term(client) -> str:
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post(f"/api/v1/terms/{term['term_id']}/select")
    return term["term_id"]


def test_time_slot_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)

    created = client.post("/api/v1/time-slots", json={**SLOT, "term_id": term_id})
    assert created.status_code == 201
    body = created.json()
    assert body["label"] == SLOT["label"]
    assert body["term_id"] == term_id
    slot_id = body["time_slot_id"]

    listed = client.get("/api/v1/time-slots", params={"term_id": term_id})
    items = listed.json()["items"]
    assert len(items) == 49
    assert items[-1]["time_slot_id"] == slot_id

    patched = client.patch(
        f"/api/v1/time-slots/{slot_id}",
        json={"label": "Renamed window"},
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "Renamed window"
    assert patched.json()["end_time"] == SLOT["end_time"]

    assert client.delete(f"/api/v1/time-slots/{slot_id}").status_code == 204


def test_writes_against_a_non_active_term_are_refused(db_session: Session):
    client = make_api_client(db_session)
    active_id = make_active_term(client)
    other = client.post(
        "/api/v1/terms", json={"academic_year": "2024-2025", "term_number": 1}
    ).json()

    refused = client.post(
        "/api/v1/time-slots", json={**SLOT, "term_id": other["term_id"]}
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "TERM_NOT_ACTIVE"
    assert refused.json()["details"] == {
        "active_term_id": active_id,
        "requested_term_id": other["term_id"],
    }


def test_writes_with_no_active_term_are_refused(db_session: Session):
    client = make_api_client(db_session)
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()

    refused = client.post("/api/v1/time-slots", json={**SLOT, "term_id": term["term_id"]})

    assert refused.status_code == 409
    assert refused.json()["code"] == "NO_ACTIVE_TERM"


def test_duplicate_ordinal_in_a_term_is_rejected(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)
    client.post("/api/v1/time-slots", json={**SLOT, "term_id": term_id})

    duplicate = client.post(
        "/api/v1/time-slots",
        json={**SLOT, "term_id": term_id, "label": "Another label"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"


def test_end_time_must_follow_start_time(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)

    rejected = client.post(
        "/api/v1/time-slots",
        json={
            **SLOT,
            "term_id": term_id,
            "start_time": "10:00:00",
            "end_time": "09:00:00",
        },
    )

    assert rejected.status_code == 422


def test_generate_canonical_slots_for_active_term(db_session: Session):
    client = make_api_client(db_session)
    active_id = make_active_term(client)

    generated = client.post(
        "/api/v1/time-slots/generate", params={"term_id": active_id}
    )
    assert generated.status_code == 200
    items = generated.json()["items"]
    assert len(items) == 48

    # Idempotent: re-generating yields the exact same 48 slots
    second = client.post(
        "/api/v1/time-slots/generate", params={"term_id": active_id}
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 48


def test_generate_canonical_slots_refused_for_inactive_term(db_session: Session):
    client = make_api_client(db_session)
    make_active_term(client)
    other = client.post(
        "/api/v1/terms", json={"academic_year": "2024-2025", "term_number": 2}
    ).json()

    refused = client.post(
        "/api/v1/time-slots/generate", params={"term_id": other["term_id"]}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "TERM_NOT_ACTIVE"


def reviewed_availability(client):
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "<FACULTY>", "is_full_time": False},
    ).json()["faculty_id"]
    path = f"/api/v1/faculty/{faculty_id}/availability"
    shown = client.get(path).json()
    slots = shown["slot_grid"]
    saved = client.put(path, json={"term_id": shown["term_id"], "time_slot_ids": [slots[0]["time_slot_id"]]})
    assert saved.status_code == 200
    assert saved.json()["reviewed"] is True
    return faculty_id, path, saved.json()


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("field,value", [
    ("ordinal", 49), ("start_time", "08:30:00"), ("end_time", "10:30:00"),
])
def test_structural_edits_preserve_slot_and_reviewed_mask(
    db_session, role, manual, field, value,
):
    client = make_api_client(db_session, role=role)
    term_id = make_active_term(client)
    if manual:
        slot = client.post(
            "/api/v1/time-slots", json={**SLOT, "term_id": term_id},
        ).json()
    else:
        slot = client.get("/api/v1/time-slots", params={"term_id": term_id}).json()["items"][0]
    faculty_id, availability_path, before = reviewed_availability(client)
    faculty = db_session.get(Faculty, faculty_id)
    mask_before = faculty.availability_mask

    refused = client.patch(
        f"/api/v1/time-slots/{slot['time_slot_id']}",
        json={field: value, "label": "Must not be applied"},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "TIME_SLOT_IMMUTABLE"
    assert refused.json()["details"] == {"fields": [field]}
    assert client.get(f"/api/v1/time-slots/{slot['time_slot_id']}").json() == slot
    assert client.get(availability_path).json() == before
    db_session.refresh(faculty)
    assert faculty.availability_mask == mask_before


def test_label_and_unchanged_structural_values_preserve_availability_review(db_session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)
    faculty_id, path, before = reviewed_availability(client)
    slot = before["slot_grid"][0]
    original = client.get(f"/api/v1/time-slots/{slot['time_slot_id']}").json()
    updated = client.patch(
        f"/api/v1/time-slots/{slot['time_slot_id']}",
        json={key: original[key] for key in ("ordinal", "start_time", "end_time")}
        | {"label": "Renamed window"},
    )
    assert updated.status_code == 200
    after = client.get(path).json()
    assert after["reviewed"] is True
    assert after["time_slot_ids"] == before["time_slot_ids"]
    assert str(db_session.get(Faculty, faculty_id).availability_reviewed_term_id) == term_id


@pytest.mark.parametrize("field", ["ordinal", "start_time", "end_time"])
def test_null_structural_values_preserve_slot_and_review(db_session, field):
    client = make_api_client(db_session)
    make_active_term(client)
    _, path, before = reviewed_availability(client)
    slot_id = before["slot_grid"][0]["time_slot_id"]
    original = client.get(f"/api/v1/time-slots/{slot_id}").json()

    response = client.patch(f"/api/v1/time-slots/{slot_id}", json={field: None})

    assert response.status_code == 200
    assert response.json() == original
    assert client.get(path).json() == before


def test_delete_and_recreate_same_size_grid_clears_review(db_session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)
    faculty_id, path, before = reviewed_availability(client)
    faculty = db_session.get(Faculty, faculty_id)
    mask_before = faculty.availability_mask
    removed = before["slot_grid"][0]["time_slot_id"]
    assert client.delete(f"/api/v1/time-slots/{removed}").status_code == 204
    assert client.get(path).json()["reviewed"] is False
    assert client.post(
        "/api/v1/time-slots", json={**SLOT, "term_id": term_id},
    ).status_code == 201

    after = client.get(path).json()
    assert len(after["slot_grid"]) == len(before["slot_grid"])
    assert after["reviewed"] is False
    db_session.refresh(faculty)
    assert faculty.availability_mask == mask_before
    assert faculty.availability_reviewed_term_id is None
    readiness = client.get(f"/api/v1/terms/{term_id}/readiness").json()
    # CHR-235: the cleared marker is informational and never blocks.
    assert not any(
        item["code"] == "FACULTY_AVAILABILITY_NOT_REVIEWED"
        for item in readiness["blockers"]
    )


def test_appending_a_slot_invalidates_review_without_rewriting_mask(db_session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)
    faculty_id, path, _ = reviewed_availability(client)
    faculty = db_session.get(Faculty, faculty_id)
    mask_before = faculty.availability_mask
    assert client.post(
        "/api/v1/time-slots", json={**SLOT, "term_id": term_id},
    ).status_code == 201
    assert client.get(path).json()["reviewed"] is False
    db_session.refresh(faculty)
    assert faculty.availability_mask == mask_before


def test_generation_invalidates_review_only_when_windows_are_added(db_session):
    client = make_api_client(db_session)
    term_id = make_active_term(client)
    _, path, before = reviewed_availability(client)
    assert client.post(
        "/api/v1/time-slots/generate", params={"term_id": term_id},
    ).status_code == 200
    assert client.get(path).json() == before

    removed = before["slot_grid"][-1]["time_slot_id"]
    assert client.delete(f"/api/v1/time-slots/{removed}").status_code == 204
    rereviewed = client.put(path, json={"term_id": before["term_id"], "time_slot_ids": before["time_slot_ids"]})
    assert rereviewed.json()["reviewed"] is True
    assert client.post(
        "/api/v1/time-slots/generate", params={"term_id": term_id},
    ).status_code == 200
    assert client.get(path).json()["reviewed"] is False
