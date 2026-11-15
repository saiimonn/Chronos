"""The Block Section API: cohorts, their classes, and the rules on both.

Capacity never appears in a request. It is derived from the expansion flag, so
the only way to change a Block's seats is to expand it.
"""

import pytest
from sqlalchemy.orm import Session

from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

SUBJECT = {
    "subject_code": "CS101",
    "subject_title": "Introduction to Computing",
    "units": 3,
    "subject_type": "LEC",
}
LAB_SUBJECT = {
    "subject_code": "CS101L",
    "subject_title": "Introduction to Computing Laboratory",
    "units": 1,
    "subject_type": "LAB",
}
PROGRAM = {"program_code": "BSCS", "program_name": "BS Computer Science"}


def prepare(client) -> str:
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post(f"/api/v1/terms/{term['term_id']}/select")
    client.post("/api/v1/subjects", json=SUBJECT)
    client.post("/api/v1/subjects", json=LAB_SUBJECT)
    client.post("/api/v1/course-programs", json=PROGRAM)
    return term["term_id"]


def block_payload(term_id: str, **overrides) -> dict:
    return {
        "term_id": term_id,
        "program_code": "BSCS",
        "year_level": 1,
        "section_letter": "A",
        **overrides,
    }


def make_offering(client, term_id: str, subject_code: str = "CS101") -> str:
    created = client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": subject_code},
    )
    return created.json()["offering_id"]


def test_block_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    created = client.post("/api/v1/block-sections", json=block_payload(term_id))
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "BSCS-1A"
    assert body["capacity"] == 20
    block_id = body["block_id"]

    listed = client.get("/api/v1/block-sections", params={"term_id": term_id})
    assert [item["block_id"] for item in listed.json()["items"]] == [block_id]

    expanded = client.patch(
        f"/api/v1/block-sections/{block_id}", json={"is_expanded": True}
    )
    assert expanded.json()["capacity"] == 25

    assert client.delete(f"/api/v1/block-sections/{block_id}").status_code == 204


def test_a_lowercase_letter_is_stored_uppercase(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    created = client.post(
        "/api/v1/block-sections", json=block_payload(term_id, section_letter="c")
    )

    assert created.json()["section_letter"] == "C"
    assert created.json()["name"] == "BSCS-1C"


def test_capacity_cannot_be_set_directly(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    block_id = client.post(
        "/api/v1/block-sections", json=block_payload(term_id)
    ).json()["block_id"]

    client.patch(f"/api/v1/block-sections/{block_id}", json={"capacity": 40})

    assert client.get(f"/api/v1/block-sections/{block_id}").json()["capacity"] == 20


def test_unknown_program_reference_is_reported(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    rejected = client.post(
        "/api/v1/block-sections", json=block_payload(term_id, program_code="NOPE")
    )

    assert rejected.status_code == 409
    assert rejected.json()["details"] == {"field": "program_code", "value": "NOPE"}


def test_duplicate_cohort_is_rejected(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    client.post("/api/v1/block-sections", json=block_payload(term_id))

    duplicate = client.post("/api/v1/block-sections", json=block_payload(term_id))

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"


def test_year_level_is_bounded(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    for year_level in (0, 6):
        rejected = client.post(
            "/api/v1/block-sections", json=block_payload(term_id, year_level=year_level)
        )
        assert rejected.status_code == 422


def test_blocks_require_the_active_term(db_session: Session):
    client = make_api_client(db_session)
    prepare(client)
    other = client.post(
        "/api/v1/terms", json={"academic_year": "2024-2025", "term_number": 1}
    ).json()

    refused = client.post(
        "/api/v1/block-sections", json=block_payload(other["term_id"])
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "TERM_NOT_ACTIVE"


def test_a_block_takes_and_drops_a_subject_offering(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    block_id = client.post(
        "/api/v1/block-sections", json=block_payload(term_id)
    ).json()["block_id"]
    offering_id = make_offering(client, term_id)

    added = client.post(
        f"/api/v1/block-sections/{block_id}/classes", json={"offering_id": offering_id}
    )
    assert added.status_code == 201
    class_id = added.json()["class_id"]
    assert added.json()["subject_code"] == "CS101"

    listed = client.get(f"/api/v1/block-sections/{block_id}/classes")
    assert [item["class_id"] for item in listed.json()["items"]] == [class_id]

    dropped = client.delete(f"/api/v1/block-sections/{block_id}/classes/{class_id}")
    assert dropped.status_code == 204


def test_a_block_cannot_take_two_groups_of_one_subject(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    block_id = client.post(
        "/api/v1/block-sections", json=block_payload(term_id)
    ).json()["block_id"]
    first = make_offering(client, term_id)
    second = make_offering(client, term_id)
    client.post(
        f"/api/v1/block-sections/{block_id}/classes", json={"offering_id": first}
    )

    rejected = client.post(
        f"/api/v1/block-sections/{block_id}/classes", json={"offering_id": second}
    )

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "DUPLICATE_RESOURCE"


def test_a_lecture_offering_serves_two_blocks_but_not_three(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    offering_id = make_offering(client, term_id)
    blocks = [
        client.post(
            "/api/v1/block-sections", json=block_payload(term_id, section_letter=letter)
        ).json()["block_id"]
        for letter in ("A", "B", "C")
    ]

    for block_id in blocks[:2]:
        added = client.post(
            f"/api/v1/block-sections/{block_id}/classes",
            json={"offering_id": offering_id},
        )
        assert added.status_code == 201

    refused = client.post(
        f"/api/v1/block-sections/{blocks[2]}/classes", json={"offering_id": offering_id}
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "OFFERING_BLOCK_LIMIT_REACHED"
    assert refused.json()["details"]["limit"] == 2


def test_a_lab_offering_serves_only_one_block(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    offering_id = make_offering(client, term_id, subject_code="CS101L")
    first, second = (
        client.post(
            "/api/v1/block-sections", json=block_payload(term_id, section_letter=letter)
        ).json()["block_id"]
        for letter in ("A", "B")
    )
    client.post(
        f"/api/v1/block-sections/{first}/classes", json={"offering_id": offering_id}
    )

    refused = client.post(
        f"/api/v1/block-sections/{second}/classes", json={"offering_id": offering_id}
    )

    assert refused.status_code == 409
    assert refused.json()["details"]["limit"] == 1
