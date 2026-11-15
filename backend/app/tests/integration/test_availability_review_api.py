"""CHR-132 / CHR-TERM-009 review stamps; CHR-235 / CHR-TERM-010 makes review optional.

The review marker is still recorded for the roster, but it never blocks
readiness or generation.
"""

import pytest
from sqlalchemy.orm import Session

from app.models import Faculty, UserRole
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

REVIEW_CODE = "FACULTY_AVAILABILITY_NOT_REVIEWED"


def make_term(client, term_number: int) -> str:
    term_id = client.post(
        "/api/v1/terms",
        json={"academic_year": "2025-2026", "term_number": term_number},
    ).json()["term_id"]
    assert client.post(f"/api/v1/terms/{term_id}/select").status_code == 200
    return term_id


def review_blocker(client, term_id: str) -> dict | None:
    body = client.get(f"/api/v1/terms/{term_id}/readiness").json()
    return next(
        (blocker for blocker in body["blockers"] if blocker["code"] == REVIEW_CODE),
        None,
    )


def test_saving_availability_reviews_it_for_the_active_term(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_term(client, 1)
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Ada Lovelace", "is_full_time": False}
    ).json()["faculty_id"]

    assert review_blocker(client, term_id) is None
    assert client.get(f"/api/v1/faculty/{faculty_id}/availability").json()[
        "reviewed"
    ] is False

    slot_ids = [
        entry["time_slot_id"]
        for entry in client.get(f"/api/v1/faculty/{faculty_id}/availability").json()[
            "slot_grid"
        ]
    ]
    saved = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": term_id, "time_slot_ids": slot_ids[:2]},
    )

    assert saved.json()["reviewed"] is True
    assert review_blocker(client, term_id) is None
    listed = client.get("/api/v1/faculty").json()["items"]
    assert listed[0]["availability_reviewed_term_id"] == term_id


def test_a_new_term_can_confirm_a_mask_but_does_not_need_to(db_session: Session):
    client = make_api_client(db_session)
    make_term(client, 1)
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Grace Hopper", "is_full_time": True}
    ).json()["faculty_id"]
    first_grid = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": first_grid["term_id"], "time_slot_ids": [first_grid["slot_grid"][0]["time_slot_id"]]},
    )
    stored = db_session.get(Faculty, faculty_id)
    db_session.refresh(stored)
    mask_before = stored.availability_mask

    second_term = make_term(client, 2)

    # The mask carries over untouched; nobody has looked at it for term 2, and
    # that no longer blocks generation.
    assert review_blocker(client, second_term) is None
    reread = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    assert reread["reviewed"] is False
    assert len(reread["time_slot_ids"]) == 1

    confirmed = client.post(
        f"/api/v1/faculty/{faculty_id}/availability/confirm",
        json={"term_id": second_term, "time_slot_ids": reread["time_slot_ids"]},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["reviewed"] is True
    assert confirmed.json()["time_slot_ids"] == reread["time_slot_ids"]
    db_session.refresh(stored)
    assert stored.availability_mask == mask_before
    assert review_blocker(client, second_term) is None


def test_a_new_member_defaults_to_open_and_can_be_confirmed(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_term(client, 1)
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Alan Turing", "is_full_time": True}
    ).json()["faculty_id"]

    shown = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    every_slot = [entry["time_slot_id"] for entry in shown["slot_grid"]]
    assert every_slot
    assert sorted(shown["time_slot_ids"]) == sorted(every_slot)
    assert shown["reviewed"] is False

    confirmed = client.post(
        f"/api/v1/faculty/{faculty_id}/availability/confirm",
        json={"term_id": term_id, "time_slot_ids": shown["time_slot_ids"]},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["reviewed"] is True
    assert review_blocker(client, term_id) is None


def test_a_mask_that_does_not_fit_the_grid_cannot_be_confirmed(db_session: Session):
    client = make_api_client(db_session)
    term_id = make_term(client, 1)
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Grace Hopper", "is_full_time": True}
    ).json()["faculty_id"]
    stored = db_session.get(Faculty, faculty_id)
    # A saved mask from a grid of a different size has nothing to keep.
    stored.availability_mask = "01"
    db_session.commit()

    refused = client.post(
        f"/api/v1/faculty/{faculty_id}/availability/confirm",
        json={"term_id": term_id, "time_slot_ids": []},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "AVAILABILITY_NEEDS_UPDATE"
    assert review_blocker(client, term_id) is None


def _faculty_with_one_slot(client) -> tuple[str, dict]:
    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Ada Lovelace", "is_full_time": True}
    ).json()["faculty_id"]
    grid = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": grid["term_id"], "time_slot_ids": [grid["slot_grid"][0]["time_slot_id"]]},
    )
    return faculty_id, grid


def test_a_confirmation_shown_for_another_term_is_refused(db_session: Session):
    client = make_api_client(db_session)
    make_term(client, 1)
    faculty_id, _ = _faculty_with_one_slot(client)
    second_term = make_term(client, 2)
    # The dialog was opened for term 2, then the operator switched to term 3.
    shown = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    third_term = make_term(client, 3)

    refused = client.post(
        f"/api/v1/faculty/{faculty_id}/availability/confirm",
        json={"term_id": second_term, "time_slot_ids": shown["time_slot_ids"]},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "AVAILABILITY_REVIEW_STALE"
    assert review_blocker(client, third_term) is None


def test_a_confirmation_of_a_replaced_selection_is_refused(db_session: Session):
    client = make_api_client(db_session)
    make_term(client, 1)
    faculty_id, _ = _faculty_with_one_slot(client)
    term_id = make_term(client, 2)
    shown = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    # Someone else replaces the mask while the dialog is still open.
    client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": shown["term_id"], "time_slot_ids": [shown["slot_grid"][1]["time_slot_id"]]},
    )
    db_session.get(Faculty, faculty_id).availability_reviewed_term_id = None
    db_session.flush()

    refused = client.post(
        f"/api/v1/faculty/{faculty_id}/availability/confirm",
        json={"term_id": term_id, "time_slot_ids": shown["time_slot_ids"]},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "AVAILABILITY_REVIEW_STALE"
    assert review_blocker(client, term_id) is None


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("empty", [False, True])
def test_stale_term_save_preserves_mask_and_review_marker(db_session, role, empty):
    client = make_api_client(db_session, role=role)
    first_term = make_term(client, 1)
    faculty_id, _ = _faculty_with_one_slot(client)
    shown = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()
    faculty = db_session.get(Faculty, faculty_id)
    before = faculty.availability_mask, faculty.availability_reviewed_term_id
    second_term = make_term(client, 2)

    refused = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": first_term, "time_slot_ids": [] if empty else shown["time_slot_ids"]},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "AVAILABILITY_REVIEW_STALE"
    db_session.refresh(faculty)
    assert (faculty.availability_mask, faculty.availability_reviewed_term_id) == before
    assert review_blocker(client, second_term) is None


def test_availability_save_requires_the_displayed_term_id(db_session):
    client = make_api_client(db_session)
    make_term(client, 1)
    faculty_id, _ = _faculty_with_one_slot(client)
    before = client.get(f"/api/v1/faculty/{faculty_id}/availability").json()

    refused = client.put(
        f"/api/v1/faculty/{faculty_id}/availability", json={"time_slot_ids": []},
    )

    assert refused.status_code == 422
    assert client.get(f"/api/v1/faculty/{faculty_id}/availability").json() == before


def test_current_term_empty_selection_is_saved_and_reviewed(db_session):
    client = make_api_client(db_session)
    term_id = make_term(client, 1)
    faculty_id, _ = _faculty_with_one_slot(client)

    saved = client.put(
        f"/api/v1/faculty/{faculty_id}/availability",
        json={"term_id": term_id, "time_slot_ids": []},
    )

    assert saved.status_code == 200
    assert saved.json()["reviewed"] is True
    assert saved.json()["time_slot_ids"] == []
    faculty = db_session.get(Faculty, faculty_id)
    db_session.refresh(faculty)
    assert faculty.availability_mask == "0" * len(saved.json()["slot_grid"])
