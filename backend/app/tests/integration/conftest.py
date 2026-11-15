"""Fixtures for tests that exercise real PostgreSQL schema behaviour.

Constraints are the thing under test here, so these tests talk to a migrated
database rather than to SQLite or to `Base.metadata.create_all`: a CHECK or a
partial unique index only behaves like production when PostgreSQL enforces it.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.tests.integration.alembic_support import run_alembic


@pytest.fixture(scope="session")
def migrated_engine(postgres_test_url: str):
    # Downgrade first so the suite is independent of whatever state
    # test_postgresql_migrations may have left the database in (it runs its
    # own downgrade/upgrade cycle against the same database).
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", "head")
    engine = create_engine(postgres_test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine) -> Session:
    """A session whose work is always rolled back, leaving no rows behind."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _relax_term_setup_cutoff(monkeypatch):
    """Let fixtures create and select terms from before AY 2026-2027.

    Most integration fixtures predate the setup cutoff and use older periods
    (curricula, snapshots and history are keyed on them). The cutoff itself is
    covered in test_terms_api.py, which restores the real value per test.
    """
    from app.services.catalog import terms

    monkeypatch.setattr(terms, "SETUP_CUTOFF_ACADEMIC_YEAR", "0001-0002")
