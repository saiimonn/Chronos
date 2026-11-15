"""Department Administrator management of local Chronos accounts."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import AppUser, UserRole
from app.schemas.auth import UserCreate, UserUpdate
from app.services.audit import record_event
from app.services.auth import revoke_user_sessions
from app.services.security import hash_password


def list_users(db: Session) -> list[AppUser]:
    return list(db.scalars(select(AppUser).order_by(AppUser.username)).all())


def _duplicate_username(username: str) -> APIError:
    return APIError(
        409,
        "DUPLICATE_RESOURCE",
        "A record with these values already exists.",
        {"conflict": {"username": username}},
    )


def create_user(db: Session, payload: UserCreate) -> AppUser:
    # ponytail: app-level check, so a concurrent case-variant insert can slip
    # past it; acceptable for admin-only creation. Upgrade path: a unique
    # index on lower(username).
    if db.scalar(
        select(AppUser.user_id).where(
            func.lower(AppUser.username) == func.lower(payload.username)
        )
    ):
        raise _duplicate_username(payload.username)
    user = AppUser(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    try:
        # A savepoint, so a taken username undoes only this insert.
        with db.begin_nested():
            db.add(user)
    except IntegrityError as error:
        raise _duplicate_username(payload.username) from error
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: UUID, payload: UserUpdate) -> AppUser:
    """Change a user's role or active state, never leaving zero active admins.

    Every writer locks all active administrator rows, in key order, before
    reading the target. A concurrent writer waits on those locks, then
    PostgreSQL re-checks the rows under READ COMMITTED, so it no longer counts
    an administrator the first writer demoted or deactivated.
    """
    active_admin_ids = set(
        db.scalars(
            select(AppUser.user_id)
            .where(
                AppUser.role == UserRole.DEPARTMENT_ADMINISTRATOR,
                AppUser.is_active,
            )
            .order_by(AppUser.user_id)
            .with_for_update()
        ).all()
    )
    user = db.scalar(
        select(AppUser)
        .where(AppUser.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise APIError(404, "NOT_FOUND", "The requested user was not found.")

    role = payload.role if payload.role is not None else user.role
    is_active = payload.is_active if payload.is_active is not None else user.is_active
    stays_admin = is_active and role == UserRole.DEPARTMENT_ADMINISTRATOR
    if active_admin_ids == {user.user_id} and not stays_admin:
        raise APIError(
            409,
            "LAST_ACTIVE_ADMINISTRATOR",
            "At least one active Department Administrator must remain.",
        )

    if user.is_active and not is_active:
        # A deactivated user's open sessions end now, not at their expiry.
        revoke_user_sessions(db, user.user_id)
    user.role = role
    user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, actor_id: UUID, user_id: UUID, new_password: str) -> None:
    """Set another account's password and end all of its sessions."""
    if user_id == actor_id:
        # Your own password is changed with the current one (auth.change_own_password).
        raise APIError(
            400, "USE_PASSWORD_CHANGE", "Change your own password with your current password."
        )
    user = db.scalar(
        select(AppUser)
        .where(AppUser.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise APIError(404, "NOT_FOUND", "The requested user was not found.")
    user.password_hash = hash_password(new_password)
    revoked = revoke_user_sessions(db, user.user_id)
    record_event(
        db,
        actor_id=actor_id,
        operation="user.password_reset",
        target_type="user",
        target_id=user.user_id,
        details={"revoked_count": revoked},
    )
    db.commit()
