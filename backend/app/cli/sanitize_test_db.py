"""Clean a copy of the production database for the test environment.

Run by ``backend/scripts/refresh_test_db.sh`` right after production is
restored into the test database (docs/TEST_ENVIRONMENT.md). It removes what
must not carry over from production:

- every login session, so no production cookie works on the test site;
- every password, replaced by the fixed test password ``testtest``
  (``TEST_PASSWORD``), so developers can sign in as any account.

Scheduling data, history and the audit trail are kept as they are, so the
test environment behaves like production. It refuses to run unless
``ENVIRONMENT=test``, so it can never touch the production database.

    ENVIRONMENT=test python -m app.cli.sanitize_test_db
"""

import sys

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import AppUser, AuthSession
from app.services.security import hash_password

REQUIRED_ENVIRONMENT = "test"
# Every account on the test site signs in with this after a refresh. It is
# not a secret: the test site holds sanitized data behind the developer gate.
TEST_PASSWORD = "testtest"


class NotTestEnvironmentError(RuntimeError):
    """The settings do not describe the test environment."""


def sanitize(db: Session) -> tuple[int, int]:
    """Drop sessions and reset passwords; return (sessions, users) changed."""
    if settings.environment != REQUIRED_ENVIRONMENT:
        raise NotTestEnvironmentError(
            f"ENVIRONMENT is {settings.environment!r}; this only runs on "
            f"{REQUIRED_ENVIRONMENT!r}"
        )
    sessions = db.execute(delete(AuthSession)).rowcount
    users = db.execute(update(AppUser).values(password_hash=hash_password(TEST_PASSWORD))).rowcount
    db.commit()
    return sessions, users


def main() -> int:
    with SessionLocal() as db:
        try:
            sessions, users = sanitize(db)
        except NotTestEnvironmentError as error:
            print(error, file=sys.stderr)
            return 2
    print(f"Removed {sessions} sessions; set the password of {users} users to {TEST_PASSWORD!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
