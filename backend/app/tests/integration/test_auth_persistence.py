from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppUser, AuthSession, UserRole
from app.services.security import generate_session_token, hash_password

pytestmark = pytest.mark.postgres


def make_user(**overrides) -> AppUser:
    values = {
        "username": "scheduler",
        "password_hash": hash_password("correct horse battery staple"),
        "role": UserRole.SCHEDULING_COMMITTEE,
    }
    values.update(overrides)
    return AppUser(**values)


def test_user_and_hashed_session_persist(db_session: Session):
    user = make_user()
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user=user,
        token_hash=token.token_hash,
        created_at=now,
        expires_at=now + timedelta(hours=8),
    )
    db_session.add(session)
    db_session.flush()

    assert user.user_id is not None
    assert session.session_id is not None
    assert session.user is user
    assert user.sessions == [session]
    assert session.token_hash == token.token_hash
    assert token.value != session.token_hash

    revoked_at = now + timedelta(hours=1)
    session.revoke(revoked_at)
    db_session.flush()
    db_session.expire(session)

    assert session.revoked_at == revoked_at
    assert not session.is_valid(revoked_at)


@pytest.mark.parametrize("role", list(UserRole))
def test_both_approved_roles_persist(db_session: Session, role: UserRole):
    db_session.add(make_user(username=role.value, role=role))
    db_session.flush()

    assert db_session.query(AppUser).filter_by(role=role).one().role == role


def test_unknown_role_is_rejected(db_session: Session):
    db_session.add(make_user(role="superuser"))

    with pytest.raises(IntegrityError, match="ck_app_user_role"):
        db_session.flush()


def test_non_argon2id_password_hash_is_rejected(db_session: Session):
    db_session.add(make_user(password_hash="plaintext-password"))

    with pytest.raises(IntegrityError, match="ck_app_user_password_hash_argon2id"):
        db_session.flush()


def test_raw_or_malformed_session_token_is_rejected(db_session: Session):
    user = make_user()
    db_session.add(user)
    db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(
        AuthSession(
            user_id=user.user_id,
            token_hash="raw-session-token",
            created_at=now,
            expires_at=now + timedelta(hours=8),
        )
    )

    with pytest.raises(IntegrityError, match="ck_auth_session_token_hash_sha256"):
        db_session.flush()


def test_session_expiry_must_follow_creation(db_session: Session):
    user = make_user()
    db_session.add(user)
    db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(
        AuthSession(
            user_id=user.user_id,
            token_hash="a" * 64,
            created_at=now,
            expires_at=now,
        )
    )

    with pytest.raises(IntegrityError, match="ck_auth_session_expiry_after_creation"):
        db_session.flush()
