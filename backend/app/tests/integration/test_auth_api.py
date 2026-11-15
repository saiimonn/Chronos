from datetime import datetime, timedelta, timezone
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.core.config import settings
from app.main import create_app
from app.models import AppUser, AuthSession, UserRole
from app.services.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
)

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery staple"


def make_user(
    db: Session,
    username: str = "scheduler",
    role: UserRole = UserRole.SCHEDULING_COMMITTEE,
    is_active: bool = True,
) -> AppUser:
    user = AppUser(
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user


def make_client(db: Session, include_role_routes: bool = False) -> TestClient:
    app = create_app()

    if include_role_routes:

        @app.get("/_test/committee")
        def committee_operation(
            user: Annotated[AppUser, Depends(require_committee_member)],
        ):
            return {"username": user.username}

        @app.get("/_test/administrator")
        def administrator_operation(
            user: Annotated[AppUser, Depends(require_department_administrator)],
        ):
            return {"username": user.username}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    # TestClient defaults to the host "testserver", which is not a trusted
    # host; the middleware would reject every request before it routes.
    return TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 50000), raise_server_exceptions=False
    )


def error_signature(response) -> tuple:
    body = response.json()
    return (
        response.status_code,
        body["code"],
        body["message"],
        tuple(sorted(body["details"].items())),
    )


def test_login_me_and_logout_use_a_hashed_revocable_session(db_session: Session):
    user = make_user(db_session)
    client = make_client(db_session)

    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
    )

    assert login.status_code == 200
    assert login.json() == {
        "user_id": str(user.user_id),
        "username": user.username,
        "role": UserRole.SCHEDULING_COMMITTEE.value,
    }
    assert "password" not in login.text.lower()
    cookie_header = login.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "path=/" in cookie_header
    assert f"max-age={settings.session_ttl_seconds}" in cookie_header

    raw_token = client.cookies.get(settings.session_cookie_name)
    persisted_session = db_session.scalar(select(AuthSession))
    assert raw_token
    assert persisted_session is not None
    assert persisted_session.token_hash == hash_session_token(raw_token)
    assert raw_token != persisted_session.token_hash

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == login.json()

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    db_session.refresh(persisted_session)
    assert persisted_session.revoked_at is not None
    assert client.get("/api/v1/auth/me").status_code == 401


def test_invalid_login_outcomes_are_indistinguishable(db_session: Session):
    make_user(db_session)
    make_user(db_session, username="inactive", is_active=False)
    client = make_client(db_session)

    responses = [
        client.post(
            "/api/v1/auth/login",
            json={"username": "missing", "password": PASSWORD},
        ),
        client.post(
            "/api/v1/auth/login",
            json={"username": "scheduler", "password": "wrong"},
        ),
        client.post(
            "/api/v1/auth/login",
            json={"username": "inactive", "password": PASSWORD},
        ),
    ]

    assert {error_signature(response) for response in responses} == {
        (401, "INVALID_CREDENTIALS", "Invalid username or password.", ())
    }


@pytest.mark.parametrize("session_state", ["expired", "revoked", "inactive_user"])
def test_invalid_session_states_are_rejected(db_session: Session, session_state: str):
    user = make_user(db_session)
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    auth_session = AuthSession(
        user=user,
        token_hash=token.token_hash,
        created_at=now - timedelta(hours=2),
        expires_at=now + timedelta(hours=1),
    )
    if session_state == "expired":
        auth_session.expires_at = now - timedelta(hours=1)
    elif session_state == "revoked":
        auth_session.revoked_at = now
    else:
        user.is_active = False
    db_session.add(auth_session)
    db_session.flush()
    client = make_client(db_session)
    client.cookies.set(settings.session_cookie_name, token.value)

    response = client.get("/api/v1/auth/me")

    assert error_signature(response) == (
        401,
        "AUTHENTICATION_REQUIRED",
        "A valid session is required.",
        (),
    )


def test_missing_and_unknown_sessions_are_rejected(db_session: Session):
    client = make_client(db_session)
    missing = client.get("/api/v1/auth/me")
    client.cookies.set(settings.session_cookie_name, "unknown-token")
    unknown = client.get("/api/v1/auth/me")

    assert error_signature(missing) == error_signature(unknown)
    assert missing.status_code == 401


def test_logout_always_clears_an_invalid_cookie(db_session: Session):
    client = make_client(db_session)
    client.cookies.set(settings.session_cookie_name, "unknown-token")

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    cookie_header = response.headers["set-cookie"].lower()
    assert f'{settings.session_cookie_name}=""' in cookie_header
    assert "max-age=0" in cookie_header
    assert "path=/" in cookie_header


@pytest.mark.parametrize(
    ("role", "committee_status", "administrator_status"),
    [
        (None, 401, 401),
        (UserRole.SCHEDULING_COMMITTEE, 200, 403),
        (UserRole.DEPARTMENT_ADMINISTRATOR, 200, 200),
    ],
)
def test_role_matrix(
    db_session: Session,
    role: UserRole | None,
    committee_status: int,
    administrator_status: int,
):
    client = make_client(db_session, include_role_routes=True)
    if role is not None:
        user = make_user(db_session, username=role.value, role=role)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": PASSWORD},
        )
        assert login.status_code == 200

    assert client.get("/_test/committee").status_code == committee_status
    assert client.get("/_test/administrator").status_code == administrator_status
