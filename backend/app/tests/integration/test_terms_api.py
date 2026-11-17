import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BlockSection, CourseProgram, HistoricalSnapshot, Term
from app.services.catalog import terms as terms_service
from app.tests.support.api import make_api_client, make_client

pytestmark = pytest.mark.postgres


def test_term_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)

    created = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["academic_year"] == "2025-2026"
    assert body["is_active"] is False
    term_id = body["term_id"]

    listed = client.get("/api/v1/terms")
    assert listed.status_code == 200
    assert [item["term_id"] for item in listed.json()["items"]] == [term_id]

    fetched = client.get(f"/api/v1/terms/{term_id}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    patched = client.patch(f"/api/v1/terms/{term_id}", json={"term_number": 2})
    assert patched.status_code == 200
    assert patched.json()["term_number"] == 2

    # Terms are archived, never deleted: there is no DELETE route.
    assert client.delete(f"/api/v1/terms/{term_id}").status_code == 405
    archived = client.post(f"/api/v1/terms/{term_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    assert client.get(f"/api/v1/terms/{term_id}").status_code == 200


def test_term_list_flags_captured_terms(db_session: Session):
    client = make_api_client(db_session)
    ids = {}
    for year in ("2023-2024", "2024-2025", "2025-2026"):
        ids[year] = client.post(
            "/api/v1/terms", json={"academic_year": year, "term_number": 1}
        ).json()["term_id"]
    # One snapshot matched by source term id, one by academic period only.
    db_session.add(
        HistoricalSnapshot(
            source_term_id=ids["2023-2024"], academic_year="1999-2000", term_number=9
        )
    )
    db_session.add(
        HistoricalSnapshot(
            source_term_id=uuid.uuid4(), academic_year="2024-2025", term_number=1
        )
    )
    db_session.flush()

    items = client.get("/api/v1/terms").json()["items"]

    assert {i["academic_year"]: i["is_captured"] for i in items} == {
        "2023-2024": True,
        "2024-2025": True,
        "2025-2026": False,
    }


def test_duplicate_academic_period_is_rejected(db_session: Session):
    client = make_api_client(db_session)
    payload = {"academic_year": "2025-2026", "term_number": 1}
    assert client.post("/api/v1/terms", json=payload).status_code == 201

    duplicate = client.post("/api/v1/terms", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"
    assert duplicate.json()["message"] == "Term 1 of AY 2025-2026 already exists."
    assert duplicate.json()["details"]["conflict"] == {
        "academic_year": "2025-2026",
        "term_number": "1",
    }


def test_selecting_a_term_deactivates_the_incumbent(db_session: Session):
    client = make_api_client(db_session)
    first = client.post(
        "/api/v1/terms", json={"academic_year": "2024-2025", "term_number": 1}
    ).json()
    second = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()

    assert client.get("/api/v1/terms/active").status_code == 409

    selected = client.post(f"/api/v1/terms/{first['term_id']}/select")
    assert selected.status_code == 200
    assert selected.json()["is_active"] is True
    assert client.get("/api/v1/terms/active").json()["term_id"] == first["term_id"]

    # Selecting the already-active term is idempotent.
    assert client.post(f"/api/v1/terms/{first['term_id']}/select").status_code == 200

    moved = client.post(f"/api/v1/terms/{second['term_id']}/select")
    assert moved.status_code == 200
    active = db_session.scalars(select(Term).where(Term.is_active)).all()
    assert [str(term.term_id) for term in active] == [second["term_id"]]


def test_a_new_term_starts_with_no_blocks_when_selected(db_session: Session):
    # Blocks are never carried over from another term: a new term is empty
    # until its Blocks are generated or added in Term Setup.
    client = make_api_client(db_session)
    old = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    db_session.add(CourseProgram(program_code="BSCS", program_name="Computer Science"))
    db_session.flush()
    db_session.add(
        BlockSection(term_id=old["term_id"], program_code="BSCS", year_level=1, section_letter="A")
    )
    db_session.flush()
    assert client.post(f"/api/v1/terms/{old['term_id']}/select").status_code == 200

    new = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 2}
    ).json()
    assert client.post(f"/api/v1/terms/{new['term_id']}/select").status_code == 200

    assert client.get("/api/v1/block-sections", params={"term_id": new["term_id"]}).json()["items"] == []
    assert len(client.get("/api/v1/block-sections", params={"term_id": old["term_id"]}).json()["items"]) == 1


def test_archiving_keeps_a_term_with_dependents_and_hides_it(db_session: Session):
    client = make_api_client(db_session)
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    db_session.add(CourseProgram(program_code="BSCS", program_name="Computer Science"))
    db_session.flush()
    db_session.add(
        BlockSection(
            term_id=term["term_id"],
            program_code="BSCS",
            year_level=1,
            section_letter="A",
        )
    )
    db_session.flush()

    archived = client.post(f"/api/v1/terms/{term['term_id']}/archive")

    assert archived.status_code == 200
    assert client.get("/api/v1/terms").json()["items"] == []
    listed = client.get("/api/v1/terms", params={"include_archived": True})
    assert [i["term_id"] for i in listed.json()["items"]] == [term["term_id"]]
    assert db_session.scalars(
        select(BlockSection).where(BlockSection.term_id == term["term_id"])
    ).one()

    restored = client.post(f"/api/v1/terms/{term['term_id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["is_archived"] is False
    assert len(client.get("/api/v1/terms").json()["items"]) == 1


def test_archived_term_cannot_be_selected_renamed_or_duplicated(db_session: Session):
    client = make_api_client(db_session)
    payload = {"academic_year": "2025-2026", "term_number": 1}
    term = client.post("/api/v1/terms", json=payload).json()
    client.post(f"/api/v1/terms/{term['term_id']}/archive")

    selected = client.post(f"/api/v1/terms/{term['term_id']}/select")
    assert selected.status_code == 409
    assert selected.json()["code"] == "TERM_SOFT_ARCHIVED"

    renamed = client.patch(f"/api/v1/terms/{term['term_id']}", json={"term_number": 2})
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "TERM_SOFT_ARCHIVED"

    duplicate = client.post("/api/v1/terms", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"
    assert duplicate.json()["details"]["is_archived"] is True
    assert "restore it" in duplicate.json()["message"]


def test_active_and_captured_terms_cannot_be_archived(db_session: Session):
    client = make_api_client(db_session)
    active = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post(f"/api/v1/terms/{active['term_id']}/select")
    captured = client.post(
        "/api/v1/terms", json={"academic_year": "2024-2025", "term_number": 1}
    ).json()
    db_session.add(
        HistoricalSnapshot(
            source_term_id=captured["term_id"], academic_year="2024-2025", term_number=1
        )
    )
    db_session.flush()

    refused_active = client.post(f"/api/v1/terms/{active['term_id']}/archive")
    assert refused_active.status_code == 409
    assert refused_active.json()["code"] == "TERM_ACTIVE"

    refused_captured = client.post(f"/api/v1/terms/{captured['term_id']}/archive")
    assert refused_captured.status_code == 409
    assert refused_captured.json()["code"] == "TERM_ARCHIVED"


@pytest.mark.parametrize("academic_year", ["2026", "2026-2028", "2026/2027", "26-27"])
def test_malformed_academic_year_is_rejected(db_session: Session, academic_year: str):
    client = make_api_client(db_session)

    refused = client.post(
        "/api/v1/terms", json={"academic_year": academic_year, "term_number": 1}
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "INVALID_ACADEMIC_YEAR"


@pytest.fixture
def real_cutoff(monkeypatch):
    # The integration conftest relaxes the cutoff for older fixtures.
    monkeypatch.setattr(terms_service, "SETUP_CUTOFF_ACADEMIC_YEAR", "2026-2027")


def test_terms_before_the_cutoff_cannot_be_set_up(db_session: Session, real_cutoff):
    client = make_api_client(db_session)

    created = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 2}
    )
    assert created.status_code == 409
    assert created.json()["code"] == "TERM_BEFORE_SETUP_CUTOFF"
    assert created.json()["details"]["cutoff_academic_year"] == "2026-2027"

    current = client.post(
        "/api/v1/terms", json={"academic_year": "2026-2027", "term_number": 1}
    )
    assert current.status_code == 201
    renamed = client.patch(
        f"/api/v1/terms/{current.json()['term_id']}",
        json={"academic_year": "2024-2025"},
    )
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "TERM_BEFORE_SETUP_CUTOFF"


def test_older_terms_from_import_exist_but_cannot_be_selected(
    db_session: Session, real_cutoff
):
    client = make_api_client(db_session)
    # Historical import and reference onboarding write terms directly.
    legacy = Term(academic_year="2023-2024", term_number=1, is_active=False)
    db_session.add(legacy)
    db_session.flush()

    listed = client.get("/api/v1/terms").json()["items"]
    assert [i["academic_year"] for i in listed] == ["2023-2024"]

    refused = client.post(f"/api/v1/terms/{legacy.term_id}/select")
    assert refused.status_code == 409
    assert refused.json()["code"] == "TERM_BEFORE_SETUP_CUTOFF"


def test_an_already_active_legacy_term_reselects_idempotently(
    db_session: Session, real_cutoff
):
    client = make_api_client(db_session)
    legacy = Term(academic_year="2023-2024", term_number=1, is_active=True)
    db_session.add(legacy)
    db_session.flush()

    assert client.post(f"/api/v1/terms/{legacy.term_id}/select").status_code == 200


def test_term_routes_require_authentication(db_session: Session):
    client = make_client(db_session)

    assert client.get("/api/v1/terms").status_code == 401
    assert client.post("/api/v1/terms", json={}).status_code == 401
