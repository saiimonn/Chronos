"""CHR-TEST-002: the seeded accounts must be idempotent and actually usable."""

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
from app.cli.seed_users import (
    PASSWORD_ENV_VAR,
    SEED_ACCOUNTS,
    main as seed_users_main,
    seed_password,
    seed_users,
)
from app.main import create_app
from app.models import AppUser, UserRole

pytestmark = pytest.mark.postgres

#: A test fixture value; the CLI itself has no default.
DEFAULT_PASSWORD = "password"


def make_client(db: Session) -> TestClient:
    """A client whose app also carries probe routes for each role guard.

    No shipped router uses `require_department_administrator` yet, so proving a
    seeded administrator clears it needs a route mounted here.
    """
    app = create_app()

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
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), raise_server_exceptions=False)


def test_seeds_one_account_per_supported_role(db_session: Session) -> None:
    seeded = seed_users(db_session, DEFAULT_PASSWORD)

    assert all(created for _, created in seeded)
    assert {user.role for user, _ in seeded} == {role.value for role in UserRole}
    assert all(user.is_active for user, _ in seeded)


def test_second_run_creates_no_duplicates(db_session: Session) -> None:
    first = seed_users(db_session, DEFAULT_PASSWORD)
    second = seed_users(db_session, DEFAULT_PASSWORD)

    assert all(created for _, created in first)
    assert not any(created for _, created in second)
    assert [user.user_id for user, _ in first] == [user.user_id for user, _ in second]

    usernames = [account.username for account in SEED_ACCOUNTS]
    rows = db_session.scalars(
        select(AppUser).where(AppUser.username.in_(usernames))
    ).all()
    assert len(rows) == len(SEED_ACCOUNTS)


def test_rerun_leaves_an_edited_account_alone(db_session: Session) -> None:
    (user, _), *_ = seed_users(db_session, DEFAULT_PASSWORD)
    original_hash = user.password_hash
    user.is_active = False
    db_session.flush()

    seed_users(db_session, "a-different-password")

    assert user.password_hash == original_hash
    assert user.is_active is False


def test_seeded_accounts_can_log_in_and_carry_their_role(db_session: Session) -> None:
    seed_users(db_session, DEFAULT_PASSWORD)
    client = make_client(db_session)

    for account in SEED_ACCOUNTS:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": account.username, "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == account.role.value

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == account.username
        client.cookies.clear()


def test_each_seeded_role_clears_exactly_its_own_guard(db_session: Session) -> None:
    """Both seeded roles clear the committee guard; only the administrator
    clears the administrator guard."""
    seed_users(db_session, DEFAULT_PASSWORD)
    client = make_client(db_session)

    def login(username: str) -> None:
        client.cookies.clear()
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200, response.text

    login("scheduler")
    assert client.get("/_test/committee").status_code == 200
    assert client.get("/_test/administrator").status_code == 403

    login("admin")
    assert client.get("/_test/committee").status_code == 200
    assert client.get("/_test/administrator").status_code == 200


def test_a_seeded_role_can_exercise_a_real_operational_endpoint(
    db_session: Session,
) -> None:
    seed_users(db_session, DEFAULT_PASSWORD)
    client = make_client(db_session)
    client.post(
        "/api/v1/auth/login",
        json={"username": "scheduler", "password": DEFAULT_PASSWORD},
    )

    created = client.post(
        "/api/v1/terms",
        json={"academic_year": "2030-2031", "term_number": 1},
    )
    assert created.status_code == 201, created.text
    assert client.get("/api/v1/terms").status_code == 200


def test_password_comes_from_the_environment_when_set(monkeypatch) -> None:
    monkeypatch.setenv(PASSWORD_ENV_VAR, "from-the-environment")
    assert seed_password() == "from-the-environment"


def test_cli_entry_point_commits_and_reports(
    db_session: Session, monkeypatch, capsys
) -> None:
    """`main` must survive its own commit: reading a seeded attribute after the
    session closes would raise DetachedInstanceError."""
    monkeypatch.setenv(PASSWORD_ENV_VAR, DEFAULT_PASSWORD)
    monkeypatch.setattr("app.cli.seed_users.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert seed_users_main() == 0
    first = capsys.readouterr().out
    for account in SEED_ACCOUNTS:
        assert account.username in first
    assert "created" in first

    assert seed_users_main() == 0
    assert "already present" in capsys.readouterr().out
