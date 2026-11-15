"""CHR-71 / CHR-RBAC-002 comprehensive role-matrix API tests.

Verifies that every operational endpoint enforces the correct authorization
boundary for unauthenticated, Scheduler, and Administrator callers.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppUser, UserRole
from app.tests.support.api import make_api_client, make_client
from app.tests.support.history import seed_historical_terms
from app.tests.support.scheduling import TERM_ID, seed_scheduling_problem

pytestmark = pytest.mark.postgres

FAKE_ID = str(uuid4())

# ── helpers ──────────────────────────────────────────────────────────────────

SCHEDULER = UserRole.SCHEDULING_COMMITTEE
ADMIN = UserRole.DEPARTMENT_ADMINISTRATOR


def _term_id(db_session: Session) -> str:
    seed_scheduling_problem(db_session)
    return str(TERM_ID)


def _arm_request(session: Session | None = None) -> dict:
    """Pass a session when the call must reach 202: the submission rejects a
    selection that names no real history. Authorization failures are decided
    first, so those tests can leave it out."""
    snapshot_ids = (
        [str(snapshot_id) for snapshot_id in seed_historical_terms(session)]
        if session is not None
        else ["00000000-0000-0000-0000-000000000001"]
    )
    return {
        "selected_snapshot_ids": snapshot_ids,
        "thresholds": {
            stream: {
                "min_support": "0.1",
                "min_confidence": "0.5",
                "min_lift": "1.0",
                "min_term_recurrence": 1,
            }
            for stream in (
                "teacher_subject",
                "teacher_day",
                "teacher_time",
                "teacher_subject_room",
            )
        },
        "notes": "role matrix test",
    }


# ── per-endpoint fixtures ────────────────────────────────────────────────────

# Each tuple: (method, path, description)
# `path` may contain {id} which gets replaced with FAKE_ID.
# `path` may contain {term} which gets replaced with a real term_id.

committee_routes = [
    ("GET", "/api/v1/terms", "list terms"),
    ("GET", "/api/v1/terms/active", "active term"),
    ("GET", f"/api/v1/terms/{FAKE_ID}", "get term by id"),
    ("GET", "/api/v1/subjects", "list subjects"),
    ("GET", "/api/v1/buildings", "list buildings"),
    ("GET", "/api/v1/rooms", "list rooms"),
    ("GET", "/api/v1/course-programs", "list programs"),
    ("GET", "/api/v1/faculty", "list faculty"),
    ("GET", f"/api/v1/faculty/{FAKE_ID}", "get faculty by id"),
    ("GET", f"/api/v1/faculty/{FAKE_ID}/availability", "get availability"),
    ("GET", f"/api/v1/subject-offerings?term_id={FAKE_ID}", "list offerings"),
    ("GET", f"/api/v1/time-slots?term_id={FAKE_ID}", "list time slots"),
    ("GET", f"/api/v1/block-sections?term_id={FAKE_ID}", "list blocks"),
    ("GET", "/api/v1/schedules", "list schedules"),
    (
        "GET",
        f"/api/v1/schedules/{FAKE_ID}/timetable?term_id={FAKE_ID}",
        "read Committee timetable projection",
    ),
    ("GET", f"/api/v1/evaluations/{FAKE_ID}/export", "export evaluation"),
    ("GET", f"/api/v1/terms/{FAKE_ID}/readiness", "term readiness"),
    ("GET", f"/api/v1/jobs/{FAKE_ID}", "get job status"),
    ("GET", "/api/v1/ga-configuration", "read active GA configuration"),
    ("GET", "/api/v1/ga-configuration/versions", "list GA configuration versions"),
]

admin_only_routes = [
    ("GET", "/api/v1/rule-sets", "list rule sets"),
    ("GET", f"/api/v1/rule-sets/{FAKE_ID}", "get rule set detail"),
    (
        "GET",
        f"/api/v1/rule-sets/{FAKE_ID}/rules?pattern_type=teacher_day",
        "list rule set rules",
    ),
    ("GET", "/api/v1/historical-uploads", "list historical uploads"),
    ("GET", f"/api/v1/historical-uploads/{FAKE_ID}", "get historical upload"),
]


# ── tests ────────────────────────────────────────────────────────────────────


class TestUnauthenticatedAccess:
    """Every non-public endpoint must reject missing sessions with 401."""

    @pytest.mark.parametrize(
        ("method", "path", "_desc"),
        committee_routes + admin_only_routes,
        ids=[desc for _, _, desc in committee_routes + admin_only_routes],
    )
    def test_unauthenticated_rejected(self, db_session: Session, method: str, path: str, _desc: str):
        client = make_client(db_session)
        response = client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} returned {response.status_code} for unauthenticated caller"
        )


class TestSchedulerAccess:
    """Scheduler (scheduling_committee) can reach committee routes but not admin-only."""

    @pytest.mark.parametrize(
        ("method", "path", "_desc"),
        committee_routes,
        ids=[desc for _, _, desc in committee_routes],
    )
    def test_scheduler_allowed(self, db_session: Session, method: str, path: str, _desc: str):
        client = make_api_client(db_session, role=SCHEDULER)
        response = client.request(method, path)
        # 200 (empty list / success), 404 (fake id), or 409 (no active term)
        # are all evidence that authorization was checked and passed.
        assert response.status_code in (200, 404, 409), (
            f"{method} {path} returned {response.status_code} for Scheduler"
        )

    @pytest.mark.parametrize(
        ("method", "path", "_desc"),
        admin_only_routes,
        ids=[desc for _, _, desc in admin_only_routes],
    )
    def test_scheduler_forbidden(self, db_session: Session, method: str, path: str, _desc: str):
        client = make_api_client(db_session, role=SCHEDULER)
        response = client.request(method, path)
        assert response.status_code == 403, (
            f"{method} {path} returned {response.status_code} for Scheduler (expected 403)"
        )


class TestAdministratorAccess:
    """Administrator (department_administrator) can reach every endpoint."""

    @pytest.mark.parametrize(
        ("method", "path", "_desc"),
        committee_routes + admin_only_routes,
        ids=[desc for _, _, desc in committee_routes + admin_only_routes],
    )
    def test_administrator_allowed(self, db_session: Session, method: str, path: str, _desc: str):
        client = make_api_client(db_session, role=ADMIN)
        response = client.request(method, path)
        # 200, 404, or 409 all prove authorization was checked and passed.
        assert response.status_code in (200, 404, 409), (
            f"{method} {path} returned {response.status_code} for Administrator"
        )


class TestJobsAuthorization:
    """Job endpoints have per-endpoint role enforcement."""

    def test_scheduler_can_submit_ga(self, db_session: Session):
        client = make_api_client(db_session, role=SCHEDULER)
        term_id = _term_id(db_session)
        resp = client.post("/api/v1/jobs/ga", json={"term_id": term_id})
        assert resp.status_code == 202

    def test_scheduler_cannot_submit_arm(self, db_session: Session):
        client = make_api_client(db_session, role=SCHEDULER)
        resp = client.post("/api/v1/jobs/arm", json=_arm_request())
        assert resp.status_code == 403

    def test_administrator_can_submit_ga(self, db_session: Session):
        client = make_api_client(db_session, role=ADMIN)
        term_id = _term_id(db_session)
        resp = client.post("/api/v1/jobs/ga", json={"term_id": term_id})
        assert resp.status_code == 202

    def test_administrator_can_submit_arm(self, db_session: Session):
        client = make_api_client(db_session, role=ADMIN)
        resp = client.post("/api/v1/jobs/arm", json=_arm_request(db_session))
        assert resp.status_code == 202

    def test_scheduler_cannot_poll_an_administrators_arm_job(self, db_session: Session):
        """A Scheduler cannot read ARM results requested by an Administrator."""
        admin = make_api_client(db_session, role=ADMIN, username="administrator")
        arm = admin.post("/api/v1/jobs/arm", json=_arm_request(db_session))
        assert arm.status_code == 202

        scheduler = make_api_client(db_session, role=SCHEDULER)
        # 404, not 403: ownership scoping filters the job out before the ARM
        # role check runs, so the job's existence is not disclosed either.
        assert scheduler.get(arm.headers["location"]).status_code == 404

    def test_arm_results_blocked_once_owner_is_no_longer_an_administrator(
        self, db_session: Session
    ):
        """The ARM role check still fires for a job the caller owns.

        Ownership scoping means this is the only way to reach the ARM guard in
        `read_job`: a Scheduler can never submit an ARM job of their own, so the
        guard is otherwise unreachable. Covering it here keeps the check honest
        if the ownership filter is ever loosened.
        """
        client = make_api_client(db_session, role=ADMIN, username="administrator")
        arm = client.post("/api/v1/jobs/arm", json=_arm_request(db_session))
        assert arm.status_code == 202
        assert client.get(arm.headers["location"]).status_code == 200

        owner = db_session.scalars(
            select(AppUser).where(AppUser.username == "administrator")
        ).one()
        owner.role = SCHEDULER.value
        db_session.flush()

        response = client.get(arm.headers["location"])
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    def test_scheduler_cannot_poll_another_users_job(self, db_session: Session):
        """A Scheduler sees only the jobs they requested."""
        owner = make_api_client(db_session, role=SCHEDULER, username="scheduler-owner")
        term_id = _term_id(db_session)
        ga = owner.post("/api/v1/jobs/ga", json={"term_id": term_id})
        assert ga.status_code == 202

        other = make_api_client(db_session, role=SCHEDULER, username="scheduler-other")
        assert other.get(ga.headers["location"]).status_code == 404

    def test_administrator_can_poll_another_users_job(self, db_session: Session):
        """The Administrator branch reads every job, not just their own."""
        owner = make_api_client(db_session, role=SCHEDULER)
        term_id = _term_id(db_session)
        ga = owner.post("/api/v1/jobs/ga", json={"term_id": term_id})
        assert ga.status_code == 202

        admin = make_api_client(db_session, role=ADMIN, username="administrator")
        response = admin.get(ga.headers["location"])
        assert response.status_code == 200
        assert response.json()["job_id"] == ga.json()["job_id"]

    def test_unauthenticated_job_endpoints_rejected(self, db_session: Session):
        client = make_client(db_session)
        fake = str(uuid4())
        assert client.post("/api/v1/jobs/ga", json={"term_id": fake}).status_code == 401
        assert client.post("/api/v1/jobs/arm", json=_arm_request()).status_code == 401
        assert client.get(f"/api/v1/jobs/{fake}").status_code == 401


class TestAuthEndpoints:
    """Auth endpoints have special public/restricted behavior."""

    def test_login_is_public(self, db_session: Session):
        client = make_client(db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "nope"},
        )
        # Invalid credentials returns 401, not 403 — proves the endpoint is reachable.
        assert resp.status_code == 401
        assert resp.json()["code"] == "INVALID_CREDENTIALS"

    def test_logout_clears_cookie(self, db_session: Session):
        client = make_client(db_session)
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 204

    def test_me_requires_authentication(self, db_session: Session):
        client = make_client(db_session)
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_me_returns_user_with_valid_session(self, db_session: Session):
        client = make_api_client(db_session, role=SCHEDULER)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["role"] == SCHEDULER.value


class TestRoleDisplay:
    """Persisted scheduling_committee role remains supported."""

    def test_scheduler_role_round_trip(self, db_session: Session):
        client = make_api_client(db_session, role=SCHEDULER)
        me = client.get("/api/v1/auth/me").json()
        assert me["role"] == "scheduling_committee"

    def test_admin_role_round_trip(self, db_session: Session):
        client = make_api_client(db_session, role=ADMIN)
        me = client.get("/api/v1/auth/me").json()
        assert me["role"] == "department_administrator"
