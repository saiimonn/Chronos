"""CHR-134 / CHR-EVAL-011: kill real synthetic workers, retain their evidence."""

from collections import Counter
import json
import os
import time

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import EvaluationBatch, EvaluationRun
from app.repositories.jobs import JobRepository
from app.services.evaluation import export_evaluation, get_evaluation
from app.services.jobs import JobOutcome
from app.tests.integration.alembic_support import run_alembic
from app.tests.integration.test_evaluation_checkpoints import claimed_evaluation


pytestmark = pytest.mark.postgres


def _faulted_child(send, kind, encoded_payload):
    import app.services.jobs.supervisor as supervisor
    from app.services.ga import GAEngine

    fault = json.loads(encoded_payload)["synthetic_fault"]
    original = GAEngine.run
    calls = 0

    def run(engine):
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == "crash":
                os._exit(23)
            time.sleep(60)
        return original(engine)

    GAEngine.run = run
    supervisor._child_main(send, kind, encoded_payload)


@pytest.mark.parametrize("fault", ["crash", "timeout", "cancelled"])
def test_spawned_interruption_preserves_completed_and_unfinished_slots(
    postgres_test_url, monkeypatch, fault,
):
    import app.services.jobs.supervisor as supervisor
    import app.worker as worker

    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", "head")
    engine = create_engine(postgres_test_url)
    # Simulate an unrelated import-time parent engine without touching runtime data.
    unrelated = create_engine(
        "postgresql://invalid:invalid@127.0.0.1:1/unrelated_test",
        connect_args={"connect_timeout": 1},
    )
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=unrelated))
    job = None
    try:
        with Session(engine) as db:
            job = claimed_evaluation(db)
        monkeypatch.setenv("DATABASE_URL", postgres_test_url)
        monkeypatch.delenv("GA_SKIP_PERSIST", raising=False)
        monkeypatch.setattr(supervisor, "_child_main", _faulted_child)

        def cancellation_requested():
            if fault != "cancelled":
                return False
            with engine.connect() as connection:
                return connection.scalar(select(EvaluationRun.evaluation_run_id).join(EvaluationBatch).where(
                    EvaluationBatch.job_id == job.job_id,
                    EvaluationRun.mode == "unguided", EvaluationRun.pair_index == 0,
                    EvaluationRun.status == "running",
                )) is not None

        outcome = supervisor.execute_job(
            "evaluation", {**job.request, "job_id": str(job.job_id),
                           "claim_token": str(job.claim_token), "synthetic_fault": fault},
            timeout_seconds=15, cancellation_requested=cancellation_requested,
        )
        assert outcome.outcome is {
            "crash": JobOutcome.CRASH, "timeout": JobOutcome.TIMEOUT,
            "cancelled": JobOutcome.CANCELLED,
        }[fault]
        monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=engine))
        worker._finish(job, outcome)
        with Session(engine) as db:
            batch_id = db.scalar(select(EvaluationBatch.evaluation_batch_id).where(EvaluationBatch.job_id == job.job_id))
            batch = get_evaluation(db, batch_id)
            assert Counter(run.status for run in batch.runs) == {"succeeded": 1, "running": 1, "pending": 18}
            complete = next(run for run in batch.runs if run.status == "succeeded")
            assert complete.candidate["assignments"] and complete.validation_report is not None
            assert JobRepository(db).get(job.job_id).progress["completed_run_count"] == 1
            artifact = json.loads(export_evaluation(batch, "json"))
            assert artifact["completed_run_count"] == 1
            assert artifact["planned_run_count"] == 20
            assert artifact["input_snapshot"] == job.request
            assert artifact["job_status"] == ("timed_out" if fault == "timeout" else "failed")
    finally:
        if job is not None:
            with engine.begin() as connection:
                connection.execute(delete(EvaluationBatch).where(EvaluationBatch.job_id == job.job_id))
        engine.dispose()
        unrelated.dispose()
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")
