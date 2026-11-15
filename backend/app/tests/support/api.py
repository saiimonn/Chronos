"""Shared fixtures for API integration tests.

Every operational endpoint sits behind authentication, so a test that wants to
exercise one needs a client already carrying a valid session. Building that in
one place keeps each resource's test file about that resource.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import settings
from app.main import create_app
from app.models import AppUser, UserRole
from app.services.auth import create_session
from app.services.security import hash_password

PASSWORD = "correct horse battery staple"


def make_user(
    db: Session,
    username: str = "scheduler",
    role: UserRole = UserRole.SCHEDULING_COMMITTEE,
) -> AppUser:
    user = AppUser(
        username=username,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def make_client(db: Session) -> TestClient:
    """An unauthenticated client bound to the test transaction."""
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    # TestClient defaults to the host "testserver", which is not a trusted
    # host; the middleware would reject every request before it routes.
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), raise_server_exceptions=False)


def make_api_client(
    db: Session,
    role: UserRole = UserRole.SCHEDULING_COMMITTEE,
    username: str = "scheduler",
) -> TestClient:
    """A client carrying a valid session cookie for a user with `role`."""
    user = make_user(db, username=username, role=role)
    created = create_session(db, user, settings.session_ttl_seconds)
    # Committed like a real login, so a request that rolls back its own work
    # (a refused, audited operation) keeps the caller signed in.
    db.commit()
    client = make_client(db)
    client.cookies.set(settings.session_cookie_name, created.token)
    return client
