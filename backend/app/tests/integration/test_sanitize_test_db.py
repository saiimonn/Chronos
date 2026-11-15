from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import sanitize_test_db
from app.core.config import settings
from app.models import AppUser, AuthSession, UserRole
from app.services.security import generate_session_token, hash_password, verify_password

pytestmark = pytest.mark.postgres


def seed(db: Session) -> None:
    now = datetime.now(timezone.utc)
    for name in ("admin-prod", "scheduler-prod"):
        user = AppUser(
            username=name,
            password_hash=hash_password(f"{name} production password"),
            role=UserRole.SCHEDULING_COMMITTEE,
        )
        db.add(
            AuthSession(
                user=user,
                token_hash=generate_session_token().token_hash,
                created_at=now,
                expires_at=now + timedelta(hours=8),
            )
        )
    db.flush()


def test_sanitize_drops_sessions_and_sets_every_password_to_testtest(db_session, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    seed(db_session)

    sessions, users = sanitize_test_db.sanitize(db_session)

    assert sessions >= 2 and users >= 2
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) == 0
    for user in db_session.scalars(select(AppUser)):
        assert verify_password("testtest", user.password_hash)


def test_sanitize_refuses_outside_the_test_environment(db_session, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    seed(db_session)

    with pytest.raises(sanitize_test_db.NotTestEnvironmentError):
        sanitize_test_db.sanitize(db_session)
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) >= 2


def test_main_needs_no_password_in_the_environment(db_session, monkeypatch, capsys):
    # A refresh always lands on testtest; CHRONOS_SEED_PASSWORD plays no part.
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.delenv("CHRONOS_SEED_PASSWORD", raising=False)
    monkeypatch.setattr(sanitize_test_db, "SessionLocal", lambda: _Borrowed(db_session))
    seed(db_session)

    assert sanitize_test_db.main() == 0
    assert "'testtest'" in capsys.readouterr().out
    for user in db_session.scalars(select(AppUser)):
        assert verify_password("testtest", user.password_hash)


class _Borrowed:
    """Lends the test's session to main() without letting it close it."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> Session:
        return self.session

    def __exit__(self, *exc) -> None:
        return None
