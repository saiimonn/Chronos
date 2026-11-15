import pytest
from sqlalchemy.orm import Session

from app.tests.support.api import make_api_client, make_client

pytestmark = pytest.mark.postgres


def test_an_empty_term_reports_every_structural_blocker(db_session: Session):
    client = make_api_client(db_session)
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()

    report = client.get(f"/api/v1/terms/{term['term_id']}/readiness")

    assert report.status_code == 200
    body = report.json()
    assert body["term_id"] == term["term_id"]
    assert body["ready"] is False
    codes = {blocker["code"] for blocker in body["blockers"]}
    assert {"NO_BLOCK_CLASSES", "NO_ROOMS"} <= codes
    # A term is created with its canonical grid, so an empty one can no longer
    # be short of slots; NO_TIME_SLOTS is now reachable only for a term whose
    # grid was emptied by hand while the write endpoints still exist.
    assert "NO_TIME_SLOTS" not in codes


def test_readiness_for_an_unknown_term_is_404(db_session: Session):
    client = make_api_client(db_session)

    missing = client.get("/api/v1/terms/00000000-0000-0000-0000-0000000000ff/readiness")

    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_readiness_requires_authentication(db_session: Session):
    client = make_client(db_session)

    unauthorized = client.get(
        "/api/v1/terms/00000000-0000-0000-0000-0000000000ff/readiness"
    )

    assert unauthorized.status_code == 401
