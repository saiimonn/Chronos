import pytest
from sqlalchemy.orm import Session

from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

LEC_SUBJECT = {
    "subject_code": "CIS 1101",
    "subject_title": "Introduction to Computing",
    "units": 3,
    "subject_type": "LEC",
}
LAB_SUBJECT = {
    "subject_code": "CIS 1102",
    "subject_title": "Computer Programming Laboratory",
    "units": 1,
    "subject_type": "LAB",
}


def make_term(client, academic_year: str = "2025-2026", term_number: int = 1) -> str:
    return client.post(
        "/api/v1/terms", json={"academic_year": academic_year, "term_number": term_number}
    ).json()["term_id"]


def prepare(client) -> str:
    term_id = make_term(client)
    client.post(f"/api/v1/terms/{term_id}/select")
    client.post("/api/v1/subjects", json=LEC_SUBJECT)
    client.post("/api/v1/subjects", json=LAB_SUBJECT)
    return term_id


def create_offering(client, term_id: str, subject_code: str = "CIS 1101"):
    return client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": subject_code},
    )


def test_group_numbers_are_assigned_in_sequence(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    created = [create_offering(client, term_id) for _ in range(3)]

    assert [response.status_code for response in created] == [201, 201, 201]
    assert [response.json()["group_no"] for response in created] == [1, 2, 3]


def test_response_identifies_an_offering_by_subject_code_and_group_no(
    db_session: Session,
):
    client = make_api_client(db_session)
    term_id = prepare(client)

    body = create_offering(client, term_id).json()

    assert body["subject_code"] == "CIS 1101"
    assert body["group_no"] == 1
    assert body["group_label"] == "CIS 1101 G1"


def test_group_numbers_restart_for_another_subject(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    create_offering(client, term_id, "CIS 1101")

    other = create_offering(client, term_id, "CIS 1102")

    assert other.json()["group_no"] == 1


def test_group_numbers_restart_for_another_term(db_session: Session):
    client = make_api_client(db_session)
    first_term = prepare(client)
    create_offering(client, first_term)
    second_term = make_term(client, academic_year="2026-2027")
    client.post(f"/api/v1/terms/{second_term}/select")

    later = create_offering(client, second_term)

    assert later.json()["group_no"] == 1


def test_offerings_list_for_one_term_ordered_by_subject_and_group(
    db_session: Session,
):
    client = make_api_client(db_session)
    term_id = prepare(client)
    create_offering(client, term_id, "CIS 1102")
    create_offering(client, term_id, "CIS 1101")
    create_offering(client, term_id, "CIS 1101")

    listed = client.get("/api/v1/subject-offerings", params={"term_id": term_id})

    assert [
        (item["subject_code"], item["group_no"]) for item in listed.json()["items"]
    ] == [("CIS 1101", 1), ("CIS 1101", 2), ("CIS 1102", 1)]


def test_offering_is_readable_by_id(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    offering_id = create_offering(client, term_id).json()["offering_id"]

    read = client.get(f"/api/v1/subject-offerings/{offering_id}")

    assert read.status_code == 200
    assert read.json()["group_label"] == "CIS 1101 G1"


def test_unknown_subject_reference_is_reported(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    rejected = create_offering(client, term_id, "NOPE")

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "UNKNOWN_REFERENCE"
    assert rejected.json()["details"] == {"field": "subject_code", "value": "NOPE"}


def test_offerings_require_the_active_term(db_session: Session):
    client = make_api_client(db_session)
    prepare(client)
    inactive_term = make_term(client, academic_year="2024-2025")

    rejected = create_offering(client, inactive_term)

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "TERM_NOT_ACTIVE"


def test_a_client_may_not_choose_the_group_no(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)

    created = client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": "CIS 1101", "group_no": 7},
    )

    assert created.status_code == 201
    assert created.json()["group_no"] == 1


def test_deleting_an_offering_leaves_survivors_numbered_as_before(
    db_session: Session,
):
    client = make_api_client(db_session)
    term_id = prepare(client)
    offerings = [create_offering(client, term_id).json() for _ in range(3)]

    deleted = client.delete(f"/api/v1/subject-offerings/{offerings[0]['offering_id']}")

    assert deleted.status_code == 204
    listed = client.get("/api/v1/subject-offerings", params={"term_id": term_id})
    assert [item["group_no"] for item in listed.json()["items"]] == [2, 3]


def test_deleting_an_offering_from_an_inactive_term_is_refused(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    offering_id = create_offering(client, term_id).json()["offering_id"]
    other_term = make_term(client, academic_year="2026-2027")
    client.post(f"/api/v1/terms/{other_term}/select")

    refused = client.delete(f"/api/v1/subject-offerings/{offering_id}")

    assert refused.status_code == 409
    assert refused.json()["code"] == "TERM_NOT_ACTIVE"


def test_missing_offering_is_a_not_found(db_session: Session):
    client = make_api_client(db_session)
    prepare(client)

    missing = client.get(
        "/api/v1/subject-offerings/00000000-0000-0000-0000-000000000000"
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"
