"""Hard Constraints page API: versioned limits that new problems use."""

import pytest
from sqlalchemy.orm import Session

from app.models import UserRole
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.tests.support.api import make_api_client
from app.tests.support.scheduling import TERM_ID, seed_scheduling_problem

pytestmark = pytest.mark.postgres

URL = "/api/v1/hard-constraints"


def _admin(db: Session):
    return make_api_client(db, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="admin")


def test_defaults_apply_until_a_version_is_saved(db_session: Session):
    body = make_api_client(db_session).get(URL).json()

    assert body["version"] is None
    assert body["limits"] == body["defaults"]
    assert body["limits"]["full_time_max_units"] == 24


def test_saved_limits_reach_new_problems(db_session: Session):
    seed_scheduling_problem(db_session)
    client = _admin(db_session)
    limits = {**client.get(URL).json()["defaults"], "max_consecutive_lectures": 2}

    saved = client.put(URL, json=limits)

    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert client.put(URL, json=limits).json()["version"] == 2
    versions = client.get(f"{URL}/versions").json()["items"]
    assert [(v["version"], v["is_active"]) for v in versions] == [(2, True), (1, False)]
    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)
    assert problem.limits.max_consecutive_lectures == 2


def test_only_administrators_save_and_bad_limits_are_refused(db_session: Session):
    scheduler = make_api_client(db_session)
    defaults = scheduler.get(URL).json()["defaults"]

    assert scheduler.put(URL, json=defaults).status_code == 403
    bad = {**defaults, "block_day_target_hours": 12}
    assert _admin(db_session).put(URL, json=bad).status_code == 422
