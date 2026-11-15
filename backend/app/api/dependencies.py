from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.db.session import SessionLocal
from app.models import AppUser, AuthSession, UserRole
from app.services.auth import find_valid_session

session_cookie = APIKeyCookie(
    name=settings.session_cookie_name,
    scheme_name="ChronosSession",
    description="Local opaque Chronos session cookie.",
    auto_error=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class AuthenticatedSession:
    user: AppUser
    session: AuthSession


def get_authenticated_session(
    token: Annotated[str | None, Security(session_cookie)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthenticatedSession:
    session = find_valid_session(db, token) if token is not None else None
    if session is None:
        raise APIError(
            401,
            "AUTHENTICATION_REQUIRED",
            "A valid session is required.",
        )
    return AuthenticatedSession(user=session.user, session=session)


def get_current_user(
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
) -> AppUser:
    return authenticated.user


def require_roles(*roles: UserRole) -> Callable[..., AppUser]:
    allowed_roles = {role.value for role in roles}

    def authorize(
        user: Annotated[AppUser, Depends(get_current_user)],
    ) -> AppUser:
        if user.role not in allowed_roles:
            raise APIError(
                403,
                "FORBIDDEN",
                "Your role does not allow this operation.",
            )
        return user

    return authorize


require_committee_member = require_roles(
    UserRole.SCHEDULING_COMMITTEE,
    UserRole.DEPARTMENT_ADMINISTRATOR,
)
require_department_administrator = require_roles(UserRole.DEPARTMENT_ADMINISTRATOR)
