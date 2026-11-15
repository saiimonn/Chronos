"""CHR-119 / CHR-CFG-001 persisted GA configuration API tests."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ExecutionJob, GAConfiguration, UserRole
from app.tests.support.api import make_api_client, make_client
from app.tests.support.scheduling import TERM_ID, seed_scheduling_problem

pytestmark = pytest.mark.postgres

DEFAULTS = {
    "population_size": 50,
    "max_generations": 100,
    "crossover_rate": 0.8,
    "mutation_rate": 0.2,
    "gene_mutation_rate": 0.1,
    "tournament_size": 3,
    "elite_count": 1,
    "stagnation_limit": 20,
    "random_seed": 0,
    "meeting_day_patterns": ["MW", "TTh", "F"],
}
# Version 1 is the CHR-119 seed (MWF, TTh). The 2026-11-09 day-pattern
# migration made version 2 (MW, TTh) active; the Friday-only migration keeps
# both on record and makes version 3 (MW, TTh, F) the active one.
SEEDED_VERSION = 3


def _admin(db: Session):
    return make_api_client(db, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="admin")


def _active_count(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(GAConfiguration).where(GAConfiguration.is_active)
    )


def test_migration_seeds_one_active_default_version(db_session: Session):
    response = make_api_client(db_session).get("/api/v1/ga-configuration")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == SEEDED_VERSION
    assert body["config"] == DEFAULTS
    assert _active_count(db_session) == 1
    original = db_session.scalar(select(GAConfiguration).where(GAConfiguration.version == 1))
    assert original.is_active is False
    assert original.parameters == {**DEFAULTS, "meeting_day_patterns": ["MWF", "TTh"]}
    two_day = db_session.scalar(select(GAConfiguration).where(GAConfiguration.version == 2))
    assert two_day.is_active is False
    assert two_day.parameters == {**DEFAULTS, "meeting_day_patterns": ["MW", "TTh"]}


def test_saving_creates_the_next_active_version(db_session: Session):
    client = _admin(db_session)

    saved = client.put(
        "/api/v1/ga-configuration",
        json={**DEFAULTS, "population_size": 80, "stagnation_limit": None},
    )

    assert saved.status_code == 200
    assert saved.json()["version"] == SEEDED_VERSION + 1
    assert saved.json()["config"]["population_size"] == 80
    assert saved.json()["config"]["stagnation_limit"] is None
    assert client.get("/api/v1/ga-configuration").json()["version"] == SEEDED_VERSION + 1
    assert _active_count(db_session) == 1
    first = db_session.scalar(
        select(GAConfiguration).where(GAConfiguration.version == SEEDED_VERSION)
    )
    assert first.is_active is False
    assert first.parameters == DEFAULTS


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"crossover_rate": 1.5}, "crossover_rate"),
        ({"population_size": 0}, "population_size"),
        ({"tournament_size": 1}, "tournament_size"),
        ({"meeting_day_patterns": []}, "meeting_day_patterns"),
        ({"meeting_day_patterns": ["MX"]}, "meeting_day_patterns"),
        ({"meeting_day_patterns": ["Su"]}, "meeting_day_patterns"),
        ({"tournament_size": 60}, None),
        ({"elite_count": 50}, None),
    ],
)
def test_out_of_range_values_are_refused_without_saving(
    db_session: Session, change: dict, field: str | None
):
    client = _admin(db_session)
    response = client.put(
        "/api/v1/ga-configuration", json={**DEFAULTS, **change}
    )

    assert response.status_code == 422
    if field is not None:
        assert any(
            error["field"] == f"body.{field}"
            for error in response.json()["details"]["errors"]
        )
    assert db_session.scalar(select(func.max(GAConfiguration.version))) == SEEDED_VERSION
    assert _active_count(db_session) == 1
    assert client.get("/api/v1/ga-configuration").json()["config"] == DEFAULTS


def test_scheduler_can_read_but_not_save(db_session: Session):
    client = make_api_client(db_session, role=UserRole.SCHEDULING_COMMITTEE)

    assert client.get("/api/v1/ga-configuration").status_code == 200
    assert client.put("/api/v1/ga-configuration", json=DEFAULTS).status_code == 403
    assert make_client(db_session).get("/api/v1/ga-configuration").status_code == 401


def test_versions_list_every_saved_version_newest_first(db_session: Session):
    client = _admin(db_session)
    client.put("/api/v1/ga-configuration", json={**DEFAULTS, "population_size": 80})

    response = client.get("/api/v1/ga-configuration/versions")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["version"] for item in items] == list(range(SEEDED_VERSION + 1, 0, -1))
    assert [item["is_active"] for item in items] == [True] + [False] * SEEDED_VERSION
    assert items[0]["config"]["population_size"] == 80
    assert items[0]["created_by_username"] == "admin"
    # Version 1 predates the current calendar but is still listed as saved.
    assert items[-1]["config"]["meeting_day_patterns"] == ["MWF", "TTh"]
    assert items[-1]["created_by_username"] is None


def test_scheduler_can_list_versions(db_session: Session):
    scheduler = make_api_client(db_session, role=UserRole.SCHEDULING_COMMITTEE)

    assert scheduler.get("/api/v1/ga-configuration/versions").status_code == 200
    assert make_client(db_session).get("/api/v1/ga-configuration/versions").status_code == 401


def test_previously_saved_days_can_be_read_and_corrected(db_session: Session):
    """CHR-125 / CHR-CFG-002 does not strand pre-existing configuration."""
    active = db_session.scalar(select(GAConfiguration).where(GAConfiguration.is_active))
    active.parameters = {**DEFAULTS, "meeting_day_patterns": ["Su"]}
    db_session.flush()
    client = _admin(db_session)

    response = client.get("/api/v1/ga-configuration")
    assert response.status_code == 200
    assert response.json()["config"]["meeting_day_patterns"] == ["Su"]
    assert client.put("/api/v1/ga-configuration", json=DEFAULTS).status_code == 200
    assert client.get("/api/v1/ga-configuration").json()["config"] == DEFAULTS


@pytest.mark.parametrize("role", list(UserRole))
def test_queued_job_keeps_the_configuration_active_at_submission(
    db_session: Session, role: UserRole
):
    admin = _admin(db_session)
    admin.put("/api/v1/ga-configuration", json={**DEFAULTS, "population_size": 70})
    seed_scheduling_problem(db_session)
    term_id = str(TERM_ID)
    submitter = (
        admin if role is UserRole.DEPARTMENT_ADMINISTRATOR else make_api_client(db_session, role=role)
    )

    accepted = submitter.post("/api/v1/jobs/ga", json={"term_id": term_id})
    admin.put("/api/v1/ga-configuration", json={**DEFAULTS, "population_size": 90})

    assert accepted.status_code == 202
    job = db_session.get(ExecutionJob, accepted.json()["job_id"])
    db_session.refresh(job)
    assert job.request["config"]["population_size"] == 70
