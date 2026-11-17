"""CHR-181 / CHR-AUTH-002 local user and role administration."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.errors import APIError
from app.models import AppUser, AuthSession, UserRole
from app.schemas.auth import UserUpdate
from app.services import users as service
from app.services import security as service_security
from app.services.auth import create_session
from app.tests.support.api import PASSWORD, make_api_client, make_client, make_user

pytestmark = pytest.mark.postgres

ADMIN = UserRole.DEPARTMENT_ADMINISTRATOR
SCHEDULER = UserRole.SCHEDULING_COMMITTEE
NEW_USER = {"username": "newcomer", "password": "a long local password", "role": SCHEDULER.value}


def _admin_client(db: Session):
    client = make_api_client(db, role=ADMIN, username="admin")
    admin = db.scalar(select(AppUser).where(AppUser.username == "admin"))
    return client, admin


@pytest.mark.parametrize(("role", "expected"), [(None, 401), (SCHEDULER, 403)])
def test_non_administrators_cannot_reach_user_administration(
    db_session: Session, role: UserRole | None, expected: int
):
    client = make_client(db_session) if role is None else make_api_client(db_session, role=role)
    target = make_user(db_session, username="target")

    responses = [
        client.get("/api/v1/users"),
        client.post("/api/v1/users", json=NEW_USER),
        client.patch(f"/api/v1/users/{target.user_id}", json={"role": ADMIN.value}),
    ]

    assert [response.status_code for response in responses] == [expected] * 3
    db_session.refresh(target)
    assert target.role == SCHEDULER
    assert db_session.scalar(select(AppUser).where(AppUser.username == "newcomer")) is None


def test_administrator_creates_a_local_argon2id_account_that_can_log_in(db_session: Session):
    client, _ = _admin_client(db_session)

    created = client.post("/api/v1/users", json=NEW_USER)

    assert created.status_code == 201
    body = created.json()
    assert body["username"] == "newcomer"
    assert body["role"] == SCHEDULER.value
    assert body["is_active"] is True
    assert "password" not in created.text.lower()
    stored = db_session.scalar(select(AppUser).where(AppUser.username == "newcomer"))
    assert stored.password_hash.startswith("$argon2id$")

    listed = client.get("/api/v1/users").json()["items"]
    assert [user["username"] for user in listed] == ["admin", "newcomer"]

    login = make_client(db_session).post(
        "/api/v1/auth/login",
        json={"username": "newcomer", "password": NEW_USER["password"]},
    )
    assert login.status_code == 200


def test_duplicate_usernames_and_short_passwords_are_refused(db_session: Session):
    client, _ = _admin_client(db_session)

    duplicate = client.post("/api/v1/users", json={**NEW_USER, "username": "admin"})
    short = client.post("/api/v1/users", json={**NEW_USER, "password": "short"})

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"
    assert short.status_code == 422


@pytest.mark.parametrize(
    "username",
    [" admin ", "admin\t", "   ", "​", "a​b", "a\x00b", "a\nb", "ａdmin", "ñoño"],
)
def test_padded_or_unprintable_usernames_are_refused(db_session: Session, username: str):
    client, _ = _admin_client(db_session)

    response = client.post("/api/v1/users", json={**NEW_USER, "username": username})

    assert response.status_code == 422
    # Only the administrator exists; a NUL can't even appear in a query.
    assert db_session.scalars(select(AppUser.username)).all() == ["admin"]


@pytest.mark.parametrize("username", ["Admin", "ADMIN"])
def test_case_variant_of_an_existing_username_is_a_duplicate(db_session: Session, username: str):
    client, _ = _admin_client(db_session)

    response = client.post("/api/v1/users", json={**NEW_USER, "username": username})

    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_RESOURCE"
    assert db_session.scalars(select(AppUser.username)).all() == ["admin"]


def test_role_assignment_takes_effect_on_the_existing_session(db_session: Session):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")
    target_client = make_client(db_session)
    created = create_session(db_session, target, settings.session_ttl_seconds)
    db_session.flush()
    target_client.cookies.set(settings.session_cookie_name, created.token)
    assert target_client.get("/api/v1/users").status_code == 403

    promoted = client.patch(f"/api/v1/users/{target.user_id}", json={"role": ADMIN.value})

    assert promoted.status_code == 200
    assert promoted.json()["role"] == ADMIN.value
    assert target_client.get("/api/v1/users").status_code == 200


def test_deactivation_revokes_sessions_and_reactivation_restores_login(db_session: Session):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")
    created = create_session(db_session, target, settings.session_ttl_seconds)
    db_session.flush()
    target_client = make_client(db_session)
    target_client.cookies.set(settings.session_cookie_name, created.token)

    deactivated = client.patch(f"/api/v1/users/{target.user_id}", json={"is_active": False})

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    db_session.refresh(created.session)
    assert created.session.revoked_at is not None
    assert target_client.get("/api/v1/auth/me").status_code == 401
    login = {"username": "target", "password": PASSWORD}
    assert make_client(db_session).post("/api/v1/auth/login", json=login).status_code == 401

    reactivated = client.patch(f"/api/v1/users/{target.user_id}", json={"is_active": True})

    assert reactivated.json()["is_active"] is True
    assert make_client(db_session).post("/api/v1/auth/login", json=login).status_code == 200
    # Reactivation does not resurrect the revoked session.
    assert target_client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize(
    "change", [{"is_active": False}, {"role": SCHEDULER.value}, {"role": SCHEDULER.value, "is_active": False}]
)
def test_the_last_active_administrator_cannot_be_removed(db_session: Session, change: dict):
    client, admin = _admin_client(db_session)
    # An inactive administrator does not count toward the invariant.
    make_user(db_session, username="dormant", role=ADMIN).is_active = False
    db_session.flush()

    response = client.patch(f"/api/v1/users/{admin.user_id}", json=change)

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_ACTIVE_ADMINISTRATOR"
    db_session.refresh(admin)
    assert (admin.role, admin.is_active) == (ADMIN, True)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_an_administrator_can_step_down_while_another_remains(db_session: Session):
    client, admin = _admin_client(db_session)
    make_user(db_session, username="second", role=ADMIN)

    response = client.patch(f"/api/v1/users/{admin.user_id}", json={"role": SCHEDULER.value})

    assert response.status_code == 200
    assert response.json()["role"] == SCHEDULER.value
    assert client.get("/api/v1/users").status_code == 403


def test_unknown_user_is_not_found(db_session: Session):
    client, _ = _admin_client(db_session)

    response = client.patch(
        "/api/v1/users/00000000-0000-0000-0000-000000000001", json={"is_active": False}
    )

    assert response.status_code == 404


@pytest.fixture
def two_committed_admins(migrated_engine):
    """Committed rows: the race is between two real transactions."""
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        admins = [make_user(session, username=name, role=ADMIN) for name in ("race-a", "race-b")]
        session.commit()
        ids = [admin.user_id for admin in admins]
    yield make_session, ids
    with make_session() as session:
        session.execute(delete(AuthSession).where(AuthSession.user_id.in_(ids)))
        session.execute(delete(AppUser).where(AppUser.user_id.in_(ids)))
        session.commit()


def test_concurrent_deactivations_leave_one_active_administrator(two_committed_admins):
    make_session, ids = two_committed_admins
    barrier = threading.Barrier(2)

    def deactivate(user_id):
        with make_session() as session:
            barrier.wait()
            try:
                service.update_user(session, user_id, UserUpdate(is_active=False))
                return "ok"
            except APIError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(deactivate, ids))

    assert outcomes == ["LAST_ACTIVE_ADMINISTRATOR", "ok"]
    with make_session() as session:
        active = session.scalars(
            select(AppUser.user_id).where(AppUser.user_id.in_(ids), AppUser.is_active)
        ).all()
    assert len(active) == 1


def test_login_racing_deactivation_never_leaves_a_session_valid_after_reactivation(
    two_committed_admins, monkeypatch
):
    """A login that read the account as active pauses; deactivation commits; it resumes."""
    from app.api import routes_auth

    make_session, ids = two_committed_admins
    target_id = ids[0]
    paused, resume = threading.Event(), threading.Event()
    real_authenticate = routes_auth.authenticate_user

    def authenticate_then_pause(db, username, password):
        user = real_authenticate(db, username, password)
        paused.set()
        assert resume.wait(10)
        return user

    monkeypatch.setattr(routes_auth, "authenticate_user", authenticate_then_pause)

    def log_in():
        with make_session() as session:
            return make_client(session).post(
                "/api/v1/auth/login", json={"username": "race-a", "password": PASSWORD}
            ).status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        login = pool.submit(log_in)
        assert paused.wait(10)
        with make_session() as session:
            service.update_user(session, target_id, UserUpdate(is_active=False))
        resume.set()
        status = login.result(timeout=10)
        with make_session() as session:
            service.update_user(session, target_id, UserUpdate(is_active=True))

    assert status == 401
    with make_session() as session:
        live = session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == target_id, AuthSession.revoked_at.is_(None)
            )
        ).all()
    assert live == []


def test_login_upgrades_an_outdated_password_hash(db_session: Session):
    from argon2 import PasswordHasher

    user = make_user(db_session, username="legacy")
    user.password_hash = PasswordHasher(memory_cost=8192, time_cost=1, parallelism=1).hash(PASSWORD)
    db_session.flush()
    old_hash = user.password_hash

    login = make_client(db_session).post(
        "/api/v1/auth/login", json={"username": "legacy", "password": PASSWORD}
    )

    assert login.status_code == 200
    db_session.refresh(user)
    assert user.password_hash != old_hash
    assert not service_security.password_needs_rehash(user.password_hash)


@pytest.mark.parametrize("body", [{"password": "a new long password"}])
def test_user_update_refuses_fields_it_cannot_change(db_session: Session, body: dict):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json=body)

    assert response.status_code == 422
    db_session.refresh(target)
    assert target.username == "target"


def test_administrator_renames_a_user_who_then_logs_in_with_the_new_name(db_session: Session):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json={"username": "j.dela.cruz"})

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "j.dela.cruz"
    assert response.json()["role"] == SCHEDULER.value
    login = make_client(db_session).post(
        "/api/v1/auth/login", json={"username": "j.dela.cruz", "password": PASSWORD}
    )
    assert login.status_code == 200
    old = make_client(db_session).post(
        "/api/v1/auth/login", json={"username": "target", "password": PASSWORD}
    )
    assert old.status_code == 401


@pytest.mark.parametrize("username", ["admin", "ADMIN"])
def test_rename_to_a_taken_username_is_a_duplicate(db_session: Session, username: str):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json={"username": username})

    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_RESOURCE"
    db_session.refresh(target)
    assert target.username == "target"


def test_a_user_may_be_renamed_to_a_new_case_of_their_own_name(db_session: Session):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json={"username": "Target"})

    assert response.status_code == 200
    assert response.json()["username"] == "Target"


@pytest.mark.parametrize("username", ["", " padded", "zero\u200bwidth"])
def test_rename_refuses_blank_padded_or_unprintable_names(db_session: Session, username: str):
    client, _ = _admin_client(db_session)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json={"username": username})

    assert response.status_code == 422
    db_session.refresh(target)
    assert target.username == "target"


def test_a_committee_member_cannot_rename_anyone(db_session: Session):
    client = make_api_client(db_session, role=SCHEDULER)
    target = make_user(db_session, username="target")

    response = client.patch(f"/api/v1/users/{target.user_id}", json={"username": "renamed"})

    assert response.status_code == 403
    db_session.refresh(target)
    assert target.username == "target"
