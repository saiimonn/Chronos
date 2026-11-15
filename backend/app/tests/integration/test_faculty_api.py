import pytest
from sqlalchemy.orm import Session

from app.models import Subject
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

FACULTY = {"full_name": "Ada Lovelace", "is_full_time": False}


def test_faculty_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)

    created = client.post("/api/v1/faculty", json=FACULTY)
    assert created.status_code == 201
    body = created.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["is_full_time"] is False
    # No slot grid exists yet, so a new record carries no availability.
    assert body["availability_mask"] == ""
    faculty_id = body["faculty_id"]

    listed = client.get("/api/v1/faculty").json()["items"]
    assert [item["faculty_id"] for item in listed] == [faculty_id]
    assert client.get(f"/api/v1/faculty/{faculty_id}").json() == body

    patched = client.patch(f"/api/v1/faculty/{faculty_id}", json={"is_full_time": True})
    assert patched.status_code == 200
    assert patched.json()["is_full_time"] is True

    # DELETE is soft: the row stays (history may name it), marked inactive.
    assert client.delete(f"/api/v1/faculty/{faculty_id}").status_code == 204
    kept = client.get(f"/api/v1/faculty/{faculty_id}")
    assert kept.status_code == 200
    assert kept.json()["is_active"] is False
    assert client.get("/api/v1/faculty").json()["items"] == []
    # Repeating it is harmless, and PATCH brings the member back.
    assert client.delete(f"/api/v1/faculty/{faculty_id}").status_code == 204
    restored = client.patch(f"/api/v1/faculty/{faculty_id}", json={"is_active": True})
    assert restored.json()["is_active"] is True


def test_preferred_days_are_soft_stored_in_calendar_order(db_session: Session):
    client = make_api_client(db_session)

    created = client.post("/api/v1/faculty", json=FACULTY).json()
    assert created["preferred_days"] == []
    faculty_id = created["faculty_id"]

    patched = client.patch(
        f"/api/v1/faculty/{faculty_id}",
        json={"preferred_days": ["WED", "MON", "MON"]},
    )
    assert patched.status_code == 200
    assert patched.json()["preferred_days"] == ["MON", "WED"]
    # The day preference never narrows the availability mask.
    assert patched.json()["availability_mask"] == ""

    # Omitting the field leaves it; an empty list clears it.
    renamed = client.patch(f"/api/v1/faculty/{faculty_id}", json={"full_name": "Ada L."})
    assert renamed.json()["preferred_days"] == ["MON", "WED"]
    cleared = client.patch(f"/api/v1/faculty/{faculty_id}", json={"preferred_days": []})
    assert cleared.json()["preferred_days"] == []


@pytest.mark.parametrize(
    "days",
    [["MON"], ["THU"], ["SAT", "SUN"], ["MON", "WED", "FRI"], ["TUE", "FRI"]],
)
def test_preferred_days_accept_any_single_days(db_session: Session, days: list[str]):
    client = make_api_client(db_session)

    created = client.post("/api/v1/faculty", json={**FACULTY, "preferred_days": days})

    assert created.status_code == 201
    assert sorted(created.json()["preferred_days"]) == sorted(days)


def test_preferred_days_reject_unknown_codes(db_session: Session):
    client = make_api_client(db_session)

    rejected = client.post("/api/v1/faculty", json={**FACULTY, "preferred_days": ["XYZ"]})

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "VALIDATION_ERROR"


def test_inactive_faculty_are_listed_only_on_request(db_session: Session):
    client = make_api_client(db_session)
    active = client.post("/api/v1/faculty", json=FACULTY).json()
    retired = client.post(
        "/api/v1/faculty", json={**FACULTY, "full_name": "Bea Retired", "is_active": False}
    ).json()
    assert active["is_active"] is True
    assert retired["is_active"] is False

    default = client.get("/api/v1/faculty").json()["items"]
    assert [item["faculty_id"] for item in default] == [active["faculty_id"]]
    everyone = client.get("/api/v1/faculty?include_inactive=true").json()["items"]
    assert [item["faculty_id"] for item in everyone] == [
        active["faculty_id"],
        retired["faculty_id"],
    ]

    reactivated = client.patch(
        f"/api/v1/faculty/{retired['faculty_id']}", json={"is_active": True}
    )
    assert reactivated.json()["is_active"] is True
    assert len(client.get("/api/v1/faculty").json()["items"]) == 2


@pytest.mark.parametrize("full_name", ["", "A" * 151])
def test_full_name_must_fit_the_stored_column(db_session: Session, full_name: str):
    client = make_api_client(db_session)

    rejected = client.post("/api/v1/faculty", json={**FACULTY, "full_name": full_name})

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "VALIDATION_ERROR"


def test_unknown_faculty_returns_404(db_session: Session):
    client = make_api_client(db_session)

    missing = client.get("/api/v1/faculty/00000000-0000-0000-0000-0000000000ff")

    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def _add_subjects(db_session: Session, *codes: str) -> None:
    for code in codes:
        db_session.add(
            Subject(
                subject_code=code,
                subject_title=f"Subject {code}",
                units=3,
                subject_type="LEC",
            )
        )
    db_session.flush()


def test_preferred_subjects_round_trip_in_code_order(db_session: Session):
    # Preferred subjects: a soft GA input stored per faculty member.
    _add_subjects(db_session, "CIS 2101", "CIS 1101", "CIS 3100")
    client = make_api_client(db_session)

    created = client.post(
        "/api/v1/faculty",
        json={**FACULTY, "preferred_subject_codes": ["CIS 2101", "CIS 1101"]},
    )
    assert created.status_code == 201
    assert created.json()["preferred_subject_codes"] == ["CIS 1101", "CIS 2101"]
    faculty_id = created.json()["faculty_id"]

    patched = client.patch(
        f"/api/v1/faculty/{faculty_id}",
        json={"preferred_subject_codes": ["CIS 3100", " CIS 3100 ", ""]},
    )
    assert patched.status_code == 200
    assert patched.json()["preferred_subject_codes"] == ["CIS 3100"]

    # Omitting the field leaves it; an empty list clears it.
    renamed = client.patch(f"/api/v1/faculty/{faculty_id}", json={"full_name": "Ada L."})
    assert renamed.json()["preferred_subject_codes"] == ["CIS 3100"]
    listed = client.get("/api/v1/faculty").json()["items"]
    assert listed[0]["preferred_subject_codes"] == ["CIS 3100"]
    cleared = client.patch(
        f"/api/v1/faculty/{faculty_id}", json={"preferred_subject_codes": []}
    )
    assert cleared.json()["preferred_subject_codes"] == []


def test_preferred_subjects_must_be_catalog_subjects(db_session: Session):
    _add_subjects(db_session, "CIS 1101")
    client = make_api_client(db_session)

    rejected = client.post(
        "/api/v1/faculty",
        json={**FACULTY, "preferred_subject_codes": ["CIS 1101", "NOPE 999"]},
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "UNKNOWN_SUBJECT"
    assert rejected.json()["details"]["subject_codes"] == ["NOPE 999"]


def test_teaching_restrictions_round_trip(db_session: Session):
    # Non-preferred subjects and lecture-only: hard GA constraints.
    _add_subjects(db_session, "CIS 1101", "CIS 2101")
    client = make_api_client(db_session)

    created = client.post(
        "/api/v1/faculty",
        json={
            **FACULTY,
            "non_preferred_subject_codes": ["CIS 2101"],
            "lecture_only": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["non_preferred_subject_codes"] == ["CIS 2101"]
    assert created.json()["lecture_only"] is True
    faculty_id = created.json()["faculty_id"]

    patched = client.patch(
        f"/api/v1/faculty/{faculty_id}",
        json={"non_preferred_subject_codes": [], "lecture_only": False},
    )
    assert patched.json()["non_preferred_subject_codes"] == []
    assert patched.json()["lecture_only"] is False


def test_subject_cannot_be_preferred_and_non_preferred(db_session: Session):
    _add_subjects(db_session, "CIS 1101")
    client = make_api_client(db_session)
    created = client.post(
        "/api/v1/faculty", json={**FACULTY, "preferred_subject_codes": ["CIS 1101"]}
    )

    rejected = client.patch(
        f"/api/v1/faculty/{created.json()['faculty_id']}",
        json={"non_preferred_subject_codes": ["CIS 1101"]},
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "SUBJECT_PREFERENCE_CONFLICT"
    assert rejected.json()["details"]["subject_codes"] == ["CIS 1101"]


def test_bulk_status_deactivates_and_reactivates_together(db_session: Session):
    client = make_api_client(db_session)
    ids = [
        client.post(
            "/api/v1/faculty", json={"full_name": name, "is_full_time": False}
        ).json()["faculty_id"]
        for name in ("Ada Lovelace", "Grace Hopper", "Alan Turing")
    ]

    off = client.post(
        "/api/v1/faculty/status", json={"faculty_ids": ids[:2], "is_active": False}
    )
    assert off.status_code == 200
    assert {item["faculty_id"] for item in off.json()["items"]} == set(ids[:2])
    assert all(item["is_active"] is False for item in off.json()["items"])
    active = [item["faculty_id"] for item in client.get("/api/v1/faculty").json()["items"]]
    assert active == [ids[2]]

    on = client.post(
        "/api/v1/faculty/status", json={"faculty_ids": ids, "is_active": True}
    )
    assert on.status_code == 200
    assert len(client.get("/api/v1/faculty").json()["items"]) == 3


def test_bulk_status_with_an_unknown_id_changes_nobody(db_session: Session):
    client = make_api_client(db_session)
    known = client.post("/api/v1/faculty", json=FACULTY).json()["faculty_id"]
    unknown = "00000000-0000-0000-0000-000000000000"

    refused = client.post(
        "/api/v1/faculty/status",
        json={"faculty_ids": [known, unknown], "is_active": False},
    )
    assert refused.status_code == 404
    assert refused.json()["details"]["faculty_ids"] == [unknown]
    assert client.get(f"/api/v1/faculty/{known}").json()["is_active"] is True
    empty = client.post(
        "/api/v1/faculty/status", json={"faculty_ids": [], "is_active": False}
    )
    assert empty.status_code == 422
