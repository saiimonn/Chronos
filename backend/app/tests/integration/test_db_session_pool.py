"""CHR-184 / CHR-FIX-014: the app engine survives a database restart."""

import pytest
from sqlalchemy import create_engine, text

from app.db.session import engine

pytestmark = pytest.mark.postgres


def test_a_stale_pooled_connection_is_replaced(postgres_test_url: str):
    if engine.url.render_as_string(hide_password=False) != postgres_test_url:
        pytest.skip("the app engine must point at TEST_DATABASE_URL (as in CI)")
    engine.dispose()  # one pooled connection, so the next checkout reuses it
    with engine.connect() as connection:
        stale_pid = connection.scalar(text("SELECT pg_backend_pid()"))

    # What a DB restart does to every pooled connection; wait for the exit.
    admin = create_engine(postgres_test_url)
    try:
        with admin.connect() as connection:
            assert connection.scalar(
                text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": stale_pid}
            )
    finally:
        admin.dispose()

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT pg_backend_pid()")) != stale_pid
