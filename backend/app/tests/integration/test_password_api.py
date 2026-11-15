"""CHR-AUTH-003 password change and administrator password reset."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from argon2 import PasswordHasher
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import AppUser, AuditEvent, AuthSession, UserRole
from app.schemas.auth import UserUpdate
from app.services import users as users_service
from app.services.auth import create_session
from app.services.security import verify_password
from app.tests.support.api import PASSWORD, make_api_client, make_client, make_user

pytestmark = pytest.mark.postgres

ADMIN = UserRole.DEPARTMENT_ADMINISTRATOR
CHANGE = "/api/v1/auth/password"
NEW = "a brand new password"
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def _second_session(db: Session, user: AppUser):
    """A client carrying a second open session for `user`."""
    created = create_session(db, user, settings.session_ttl_seconds)
    db.commit()
    client = make_client(db)
    client.cookies.set(settings.session_cookie_name, created.token)
    return client


def _events(db: Session, operation: str, target_id) -> list[AuditEvent]:
    """The operation's events for one target; the append-only table keeps other tests' rows."""
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.operation == operation, AuditEvent.target_id == str(target_id)
            )
        )
    )


def _can_log_in(db: Session, username: str, password: str) -> bool:
    login = {"username": username, "password": password}
    return make_client(db).post("/api/v1/auth/login", json=login).status_code == 200


def _user(db: Session, username: str) -> AppUser:
    return db.scalar(select(AppUser).where(AppUser.username == username))


def test_change_requires_a_session(db_session: Session):
    body = {"current_password": PASSWORD, "new_password": NEW}
    assert make_client(db_session).post(CHANGE, json=body).status_code == 401


def test_change_keeps_this_session_and_revokes_the_others(db_session: Session):
    client = make_api_client(db_session)
    user = _user(db_session, "scheduler")
    other = _second_session(db_session, user)

    response = client.post(CHANGE, json={"current_password": PASSWORD, "new_password": NEW})

    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    assert _can_log_in(db_session, "scheduler", NEW)
    assert not _can_log_in(db_session, "scheduler", PASSWORD)
    db_session.refresh(user)
    assert user.password_hash.startswith("$argon2id$")


@pytest.mark.parametrize(
    "body",
    [
        {"current_password": "not the password", "new_password": NEW},
        {"current_password": PASSWORD, "new_password": "short"},
        {"current_password": "", "new_password": NEW},
        {"current_password": PASSWORD, "new_password": NEW, "role": "department_administrator"},
    ],
)
def test_refused_change_leaves_the_password_and_sessions_alone(db_session: Session, body: dict):
    client = make_api_client(db_session)
    user = _user(db_session, "scheduler")
    other = _second_session(db_session, user)

    response = client.post(CHANGE, json=body)

    assert response.status_code in (400, 422)
    assert other.get("/api/v1/auth/me").status_code == 200
    assert verify_password(PASSWORD, user.password_hash)


def test_wrong_current_password_gives_a_generic_audited_refusal(db_session: Session):
    client = make_api_client(db_session)
    user = _user(db_session, "scheduler")

    response = client.post(CHANGE, json={"current_password": "wrong password", "new_password": NEW})

    assert response.status_code == 400
    assert response.json()["code"] == "PASSWORD_CHANGE_REFUSED"
    assert "current" not in response.json()["message"].lower()
    (event,) = _events(db_session, "user.password_change", user.user_id)
    assert event.outcome == "rejected"
    assert event.target_id == str(event.actor_user_id)
    assert "wrong password" not in str(event.details) and NEW not in str(event.details)


def test_successful_change_is_audited_without_password_data(db_session: Session):
    client = make_api_client(db_session)
    user = _user(db_session, "scheduler")
    _second_session(db_session, user)

    client.post(CHANGE, json={"current_password": PASSWORD, "new_password": NEW})

    (event,) = _events(db_session, "user.password_change", user.user_id)
    assert (event.outcome, event.target_type) == ("success", "user")
    assert event.actor_user_id == user.user_id and event.target_id == str(user.user_id)
    assert event.details == {"revoked_count": 1}


def test_administrator_resets_another_password_and_revokes_its_sessions(db_session: Session):
    client = make_api_client(db_session, role=ADMIN, username="admin")
    target = make_user(db_session, username="target")
    target_client = _second_session(db_session, target)

    response = client.post(f"/api/v1/users/{target.user_id}/password", json={"new_password": NEW})

    assert response.status_code == 204
    assert target_client.get("/api/v1/auth/me").status_code == 401
    assert _can_log_in(db_session, "target", NEW)
    assert not _can_log_in(db_session, "target", PASSWORD)
    assert client.get("/api/v1/auth/me").status_code == 200
    (event,) = _events(db_session, "user.password_reset", target.user_id)
    assert (event.outcome, event.actor_user_id) == ("success", _user(db_session, "admin").user_id)
    assert event.target_id == str(target.user_id)
    assert event.details == {"revoked_count": 1}


@pytest.mark.parametrize(("role", "expected"), [(None, 401), (UserRole.SCHEDULING_COMMITTEE, 403)])
def test_only_administrators_can_reset_passwords(
    db_session: Session, role: UserRole | None, expected: int
):
    client = make_client(db_session) if role is None else make_api_client(db_session, role=role)
    target = make_user(db_session, username="target")

    response = client.post(f"/api/v1/users/{target.user_id}/password", json={"new_password": NEW})

    assert response.status_code == expected
    assert _can_log_in(db_session, "target", PASSWORD)


def test_reset_validates_the_password_and_refuses_unknown_and_own_accounts(db_session: Session):
    client = make_api_client(db_session, role=ADMIN, username="admin")
    admin = _user(db_session, "admin")
    target = make_user(db_session, username="target")
    # Refusals roll back, so the target must be committed to survive them.
    db_session.commit()
    url = f"/api/v1/users/{target.user_id}/password"

    short = client.post(url, json={"new_password": "short"})
    extra = client.post(url, json={"new_password": NEW, "role": "x"})
    unknown = client.post(f"/api/v1/users/{UNKNOWN}/password", json={"new_password": NEW})
    own = client.post(f"/api/v1/users/{admin.user_id}/password", json={"new_password": NEW})

    assert [r.status_code for r in (short, extra, unknown, own)] == [422, 422, 404, 400]
    assert own.json()["code"] == "USE_PASSWORD_CHANGE"
    assert _can_log_in(db_session, "target", PASSWORD)
    # Only the unknown-user and own-account refusals reach the service, so only they are audited.
    for refused in (UNKNOWN, admin.user_id):
        assert [e.outcome for e in _events(db_session, "user.password_reset", refused)] == ["rejected"]


@pytest.fixture
def committed_target(migrated_engine):
    """A committed account with an outdated hash: the races are between real transactions."""
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        target = make_user(session, username="race-target")
        target.password_hash = PasswordHasher(
            memory_cost=8192, time_cost=1, parallelism=1
        ).hash(PASSWORD)
        session.commit()
        target_id = target.user_id
    yield make_session, target_id
    with make_session() as session:
        session.execute(delete(AuthSession).where(AuthSession.user_id == target_id))
        session.execute(delete(AppUser).where(AppUser.user_id == target_id))
        session.commit()


def test_login_racing_a_reset_gets_no_session_and_cannot_overwrite_the_new_hash(
    committed_target, monkeypatch
):
    """A login that verified the old hash pauses; the reset commits; it resumes."""
    from app.api import routes_auth

    make_session, target_id = committed_target
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
                "/api/v1/auth/login", json={"username": "race-target", "password": PASSWORD}
            ).status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        login = pool.submit(log_in)
        assert paused.wait(10)
        with make_session() as session:
            # No actor: the audit table is append-only, so an actor row could not be cleaned up.
            users_service.reset_password(session, None, target_id, NEW)
        resume.set()
        status = login.result(timeout=10)

    assert status == 401
    with make_session() as session:
        live = session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == target_id, AuthSession.revoked_at.is_(None)
            )
        ).all()
        stored = session.scalar(select(AppUser.password_hash).where(AppUser.user_id == target_id))
    assert live == []
    assert verify_password(NEW, stored) and not verify_password(PASSWORD, stored)


def test_change_racing_deactivation_is_refused_and_keeps_the_old_password(
    committed_target, monkeypatch
):
    from app.api import routes_auth

    make_session, target_id = committed_target
    with make_session() as session:
        target = session.get(AppUser, target_id)
        created = create_session(session, target, settings.session_ttl_seconds)
        session.commit()
        token = created.token
    paused, resume = threading.Event(), threading.Event()
    real_change = routes_auth.change_own_password

    def change_then_pause(*args, **kwargs):
        paused.set()
        assert resume.wait(10)
        return real_change(*args, **kwargs)

    monkeypatch.setattr(routes_auth, "change_own_password", change_then_pause)

    def change():
        with make_session() as session:
            client = make_client(session)
            client.cookies.set(settings.session_cookie_name, token)
            return client.post(CHANGE, json={"current_password": PASSWORD, "new_password": NEW})

    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(change)
        assert paused.wait(10)
        with make_session() as session:
            users_service.update_user(session, target_id, UserUpdate(is_active=False))
        resume.set()
        response = request.result(timeout=10)

    assert response.status_code == 401
    with make_session() as session:
        stored = session.scalar(select(AppUser.password_hash).where(AppUser.user_id == target_id))
    assert verify_password(PASSWORD, stored)


def test_concurrent_logins_on_an_outdated_hash_both_succeed(committed_target, monkeypatch):
    """Both verify the old hash; the second waits for the first's rehash, then still gets in."""
    from app.api import routes_auth

    make_session, _ = committed_target
    both_verified = threading.Barrier(2)
    real_authenticate = routes_auth.authenticate_user

    def authenticate_then_meet(db, username, password):
        user = real_authenticate(db, username, password)
        both_verified.wait(10)
        return user

    monkeypatch.setattr(routes_auth, "authenticate_user", authenticate_then_meet)

    def log_in():
        with make_session() as session:
            return make_client(session).post(
                "/api/v1/auth/login", json={"username": "race-target", "password": PASSWORD}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: log_in(), range(2)))

    assert statuses == [200, 200]
