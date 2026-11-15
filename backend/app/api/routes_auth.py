from typing import Annotated

from fastapi import APIRouter, Depends, Response, Security, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    AuthenticatedSession,
    get_authenticated_session,
    get_current_user,
    get_db,
    session_cookie,
)
from app.core.config import settings
from app.core.errors import APIError
from app.models import AppUser
from app.schemas.auth import LoginRequest, PasswordChange, UserResponse
from app.services.auth import (
    authenticate_user,
    change_own_password,
    create_session,
    find_session,
)
from app.services.security import hash_password, password_needs_rehash, verify_password
from app.services.audit import audit_rejections

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=UserResponse)
def login(
    credentials: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AppUser:
    user = authenticate_user(db, credentials.username, credentials.password)
    if user is None:
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid username or password.")

    # Same row lock the deactivation, change and reset paths take: a login
    # that verified the account either commits its session before their
    # revoke sweep or sees their change here, never inserts a session after
    # the sweep. A hash that changed while it waited is checked again: a
    # concurrent login's rehash still accepts this password, a reset or
    # change does not.
    verified_hash = user.password_hash
    db.refresh(user, with_for_update=True)
    if not user.is_active or (
        user.password_hash != verified_hash
        and not verify_password(credentials.password, user.password_hash)
    ):
        db.rollback()
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(credentials.password)

    created = create_session(db, user, settings.session_ttl_seconds)
    db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=created.token,
        max_age=settings.session_ttl_seconds,
        expires=created.session.expires_at,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    token: Annotated[str | None, Security(session_cookie)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    if token is None:
        return

    session = find_session(db, token)
    if session is not None and session.revoked_at is None:
        session.revoke()
        db.commit()


@router.get("/me", response_model=UserResponse)
def get_me(
    user: Annotated[AppUser, Depends(get_current_user)],
) -> AppUser:
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    user_id = authenticated.user.user_id
    with audit_rejections(
        db,
        actor_id=user_id,
        operation="user.password_change",
        target_type="user",
        target_id=user_id,
    ):
        change_own_password(
            db,
            user_id,
            authenticated.session.session_id,
            payload.current_password,
            payload.new_password,
        )
