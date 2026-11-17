"""GET /schedules/{id}/metrics reports feasibility and utilization metrics."""

import pytest
from sqlalchemy.orm import Session

from app.tests.support.api import make_api_client
from app.tests.support.scheduling import BLOCK_ID, CLASS_ID, seed_scheduling_problem
from app.tests.integration.test_master_block_locks import _approve, _master

pytestmark = pytest.mark.postgres


def test_metrics_of_a_master_schedule(db_session: Session):
    seed_scheduling_problem(db_session)
    db_session.commit()
    client = make_api_client(db_session)
    schedule_id = _master(client)

    empty = client.get(f"/api/v1/schedules/{schedule_id}/metrics")
    assert empty.status_code == 200, empty.text
    assert empty.json()["feasibility"]["valid"] is False
    assert empty.json()["time"]["sessions"] == 0

    assert _approve(client, schedule_id, BLOCK_ID, CLASS_ID).status_code == 200
    body = client.get(f"/api/v1/schedules/{schedule_id}/metrics").json()

    assert body["feasibility"] == {
        "required_class_count": 1,
        "assigned_class_count": 1,
        "completeness": 1.0,
        "hard_conflict_count": 0,
        "valid": True,
    }
    all_rooms = next(item for item in body["rooms"] if item["room_type"] == "all")
    assert all_rooms["rooms_used"] == 1
    assert 0 < all_rooms["frequency"]["value"] <= 1
    assert body["faculty"]["faculty_teaching"] == 1
    assert body["time"]["sessions"] == 1


def test_metrics_of_an_unknown_schedule_is_not_found(db_session: Session):
    seed_scheduling_problem(db_session)
    db_session.commit()
    client = make_api_client(db_session)
    response = client.get(
        "/api/v1/schedules/00000000-0000-0000-0000-00000000dead/metrics"
    )
    assert response.status_code == 404
