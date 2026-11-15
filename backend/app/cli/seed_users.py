"""Seed the deterministic development accounts used to exercise Chronos locally.

A migrated database starts with an empty ``app_user`` table and the API exposes
no registration route, so a fresh checkout has no way to reach the UI or to call
an authenticated endpoint. This module provisions one account per supported role
so both role branches can be exercised.

These are development fixtures, not institutional records. The usernames are
generic role labels rather than DCISM personnel, and the module writes only to
``app_user``: current-term data and the immutable historical snapshots are
populated through their own paths and are never touched here.

Run it after the schema is up to date::

    alembic upgrade head
    python -m app.cli.seed_users
"""

import os
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import AppUser, UserRole
from app.schemas.auth import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.services.security import hash_password

#: The seeded password. Required: there is no default, so no deployment can
#: end up with a well-known administrator password.
PASSWORD_ENV_VAR = "CHRONOS_SEED_PASSWORD"


@dataclass(frozen=True)
class SeedAccount:
    username: str
    role: UserRole


#: One account per role in `UserRole`, so role-gated routes can be exercised as
#: either kind of user. A new role must gain an entry here.
SEED_ACCOUNTS: tuple[SeedAccount, ...] = (
    SeedAccount(username="admin", role=UserRole.DEPARTMENT_ADMINISTRATOR),
    SeedAccount(username="scheduler", role=UserRole.SCHEDULING_COMMITTEE),
)


class SeedPasswordError(ValueError):
    """The seed password is missing or too short to seed with."""


def seed_password() -> str:
    """The password for new seed accounts, from ``CHRONOS_SEED_PASSWORD``.

    It must be set and fit the API's length limits, or an account would be
    created that the login form could never accept.
    """
    password = os.environ.get(PASSWORD_ENV_VAR, "")
    if not password.strip():
        raise SeedPasswordError(f"{PASSWORD_ENV_VAR} must be set")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise SeedPasswordError(
            f"{PASSWORD_ENV_VAR} must be {MIN_PASSWORD_LENGTH} to "
            f"{MAX_PASSWORD_LENGTH} characters"
        )
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise SeedPasswordError(f"{PASSWORD_ENV_VAR} must be valid UTF-8") from None
    return password


def seed_users(db: Session, password: str) -> list[tuple[AppUser, bool]]:
    """Provision the seed accounts, returning each user and whether it is new.

    Idempotent: an existing username is returned untouched. Re-running never
    resets a password, reactivates a disabled account, or changes a role, so a
    developer's local edits to a seeded account survive a second run.
    """
    results: list[tuple[AppUser, bool]] = []
    for account in SEED_ACCOUNTS:
        existing = db.scalar(
            select(AppUser).where(AppUser.username == account.username)
        )
        if existing is not None:
            results.append((existing, False))
            continue
        user = AppUser(
            username=account.username,
            password_hash=hash_password(password),
            role=account.role,
            is_active=True,
        )
        db.add(user)
        db.flush()
        results.append((user, True))
    return results


def main() -> int:
    try:
        password = seed_password()
    except SeedPasswordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"password source: {PASSWORD_ENV_VAR}")
    db = SessionLocal()
    try:
        seeded = seed_users(db, password)
        db.commit()
        # `commit` expires every attribute, so read them back into plain values
        # while the session is still open; the instances go detached on close.
        report = [
            (user.username, str(user.role), created) for user, created in seeded
        ]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for username, role, created in report:
        state = "created" if created else "already present"
        print(f"{username:<12} {role:<26} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
