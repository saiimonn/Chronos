"""CHR-134 / CHR-EVAL-011: preserve legacy evidence and refuse lossy rollback."""

import subprocess
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

from app.tests.integration.alembic_support import run_alembic


pytestmark = pytest.mark.postgres
OLD = "d9f1b3c5e7a2"
NEW = "e3c5a7d9b1f4"


def test_forward_migration_preserves_legacy_rows_and_rolls_back_without_reinterpreting_them(postgres_test_url):
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", OLD)
    engine = create_engine(postgres_test_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO term (term_id, academic_year, term_number, is_active) "
                "VALUES (:id, '2024-2025', 1, false)"
            ), {"id": UUID(int=134)})
            connection.execute(text(
                "INSERT INTO evaluation_batch (evaluation_batch_id, term_id, input_fingerprint, config) "
                "VALUES (:id, :term, :fingerprint, '{}'::jsonb)"
            ), {"id": UUID(int=135), "term": UUID(int=134), "fingerprint": "a" * 64})
            connection.execute(text(
                "INSERT INTO evaluation_run (evaluation_run_id, evaluation_batch_id, pair_index, mode, random_seed, status, error_message) "
                "VALUES (:id, :batch, 0, 'guided', 0, 'failed', 'retained legacy failure')"
            ), {"id": UUID(int=136), "batch": UUID(int=135)})
            before = {table: dict(connection.execute(text(f"SELECT * FROM {table}")).mappings().one())
                      for table in ("evaluation_batch", "evaluation_run")}
        run_alembic(postgres_test_url, "upgrade", NEW)
        with engine.connect() as connection:
            for table, original in before.items():
                row = connection.execute(text(f"SELECT * FROM {table}")).mappings().one()
                assert {key: row[key] for key in original} == original
                extras = ("job_id", "input_snapshot") if table == "evaluation_batch" else ("candidate", "validation_report")
                assert all(row[key] is None for key in extras)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO evaluation_run (evaluation_run_id, evaluation_batch_id, pair_index, mode, random_seed, status) "
                "VALUES (:id, :batch, 0, 'unguided', 0, 'pending')"
            ), {"id": UUID(int=137), "batch": UUID(int=135)})
        with pytest.raises(subprocess.CalledProcessError):
            run_alembic(postgres_test_url, "downgrade", OLD)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW
            assert connection.scalar(text("SELECT status FROM evaluation_run WHERE evaluation_run_id = :id"), {"id": UUID(int=137)}) == "pending"
            connection.execute(text("DELETE FROM evaluation_run WHERE evaluation_run_id = :id"), {"id": UUID(int=137)})
        run_alembic(postgres_test_url, "downgrade", OLD)
        with engine.connect() as connection:
            for table, original in before.items():
                assert dict(connection.execute(text(f"SELECT * FROM {table}")).mappings().one()) == original
    finally:
        engine.dispose()
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")
