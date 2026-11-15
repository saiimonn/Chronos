from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.core.errors import APIError
from app.models import AppUser, AuthSession
from app.services.audit import record_event
from app.services.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)

_DUMMY_PASSWORD_HASH = hash_password("chronos-invalid-credential-placeholder")


@dataclass(frozen=True)
class CreatedSession:
    session: AuthSession
    token: str


def authenticate_user(db: Session, username: str, password: str) -> AppUser | None:
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(password, password_hash)

    if user is None or not password_valid or not user.is_active:
        return None
    # The rehash is the caller's, once it holds the row lock and knows the
    # hash it verified is still the stored one.
    return user


def create_session(
    db: Session,
    user: AppUser,
    ttl_seconds: int,
    now: datetime | None = None,
) -> CreatedSession:
    created_at = now or datetime.now(timezone.utc)
    token = generate_session_token()
    session = AuthSession(
        user=user,
        token_hash=token.token_hash,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=ttl_seconds),
    )
    db.add(session)
    return CreatedSession(session=session, token=token.value)


def find_session(db: Session, token: str) -> AuthSession | None:
    return db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(AuthSession.token_hash == hash_session_token(token))
    )


def find_valid_session(
    db: Session, token: str, now: datetime | None = None
) -> AuthSession | None:
    session = find_session(db, token)
    if session is None or not session.is_valid(now) or not session.user.is_active:
        return None
    return session


def revoke_user_sessions(db: Session, user_id: UUID, keep: UUID | None = None) -> int:
    """End a user's open sessions now, except `keep`; returns how many."""
    statement = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if keep is not None:
        statement = statement.where(AuthSession.session_id != keep)
    return db.execute(
        statement.values(revoked_at=datetime.now(timezone.utc))
    ).rowcount


def change_own_password(
    db: Session, user_id: UUID, keep_session: UUID, current: str, new: str
) -> None:
    """Replace a user's password, ending every session but the current one."""
    # Row lock, like deactivation: two changes cannot interleave.
    user = db.scalar(
        select(AppUser)
        .where(AppUser.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # Deactivation and reset revoke sessions under this same lock, so a change
    # that waited on it re-checks the account and its own session here.
    session = db.get(AuthSession, keep_session, populate_existing=True)
    if (
        user is None
        or not user.is_active
        or session is None
        or session.revoked_at is not None
    ):
        raise APIError(401, "AUTHENTICATION_REQUIRED", "A valid session is required.")
    # One message for every refusal, so it never says which part was wrong.
    if not verify_password(current, user.password_hash):
        raise APIError(400, "PASSWORD_CHANGE_REFUSED", "The password could not be changed.")
    user.password_hash = hash_password(new)
    revoked = revoke_user_sessions(db, user.user_id, keep=keep_session)
    record_event(
        db,
        actor_id=user.user_id,
        operation="user.password_change",
        target_type="user",
        target_id=user.user_id,
        details={"revoked_count": revoked},
    )
    db.commit()
