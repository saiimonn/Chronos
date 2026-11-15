"""CHR-134 / CHR-EVAL-011: never export a terminal status with an older ledger."""

import json
import threading

import pytest
from sqlalchemy import create_engine, delete, event, func, text, update
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import EvaluationBatch, ExecutionJob
from app.repositories.jobs import JobRepository
from app.services.evaluation import (
    EvaluationMode, EvaluationRunRecord, EvaluationStatus, begin_evaluation, checkpoint_evaluation_run,
    export_evaluation, get_evaluation, run_matched_evaluation,
)
from app.services.ga import GAEngine
from app.services.ga.rule_scoring import RuleBackedHistoricalScorer
from app.services.jobs.codecs import decode_ga_config, decode_guidance_snapshot, decode_scheduling_problem
from app.tests.integration.alembic_support import run_alembic
from app.tests.integration.test_evaluation_checkpoints import claimed_evaluation


pytestmark = pytest.mark.postgres


def test_completion_during_export_is_refused_or_has_the_complete_ledger(postgres_test_url):
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", "head")
    engine = create_engine(postgres_test_url)
    job = None
    try:
        with Session(engine) as writer:
            job = claimed_evaluation(writer)
            batch_id, payload = begin_evaluation(writer, job.job_id, job.claim_token)
            problem = decode_scheduling_problem(payload["problem"])
            scorer = RuleBackedHistoricalScorer(decode_guidance_snapshot(payload))
            final = []

            def checkpoint(record):
                if record.pair_index == 9 and record.mode is EvaluationMode.UNGUIDED and record.status is not EvaluationStatus.RUNNING:
                    final.append(record)
                else:
                    checkpoint_evaluation_run(writer, job.job_id, job.claim_token, batch_id, record)

            def runner(mode, config):
                return GAEngine(problem, config, historical_scorer=scorer if mode is EvaluationMode.GUIDED else None).run()

            run_matched_evaluation(
                problem, payload["input_fingerprint"], decode_ga_config(payload["config"]),
                payload["seeds"], runner, lambda candidate: scorer.score(problem, candidate.assignments),
                checkpoint=checkpoint,
            )
            assert len(final) == 1

        with engine.connect() as reader_connection, Session(bind=reader_connection) as reader:
            assert reader_connection.get_isolation_level() == "READ COMMITTED"
            completed = False

            def finish_after_ledger_read(connection, cursor, statement, parameters, context, executemany):
                nonlocal completed
                if connection is reader_connection and "from evaluation_run" in statement.lower() and not completed:
                    completed = True
                    # The reader's SELECT already has its pre-completion row snapshot.
                    with Session(engine) as finisher:
                        checkpoint_evaluation_run(finisher, job.job_id, job.claim_token, batch_id, final[0])
                        assert JobRepository(finisher).complete(job.job_id, job.claim_token, result={"evaluation_batch_id": str(batch_id)}) is not None
                        finisher.commit()

            event.listen(engine, "after_cursor_execute", finish_after_ledger_read)
            try:
                batch = get_evaluation(reader, batch_id)
                try:
                    artifact = json.loads(export_evaluation(batch, "json"))
                except APIError as error:
                    assert error.status_code == 409 and error.code == "EVALUATION_RUNNING"
                else:
                    assert artifact["completed_run_count"] == 20
                    assert all(row["status"] == "succeeded" for row in artifact["runs"])
            finally:
                event.remove(engine, "after_cursor_execute", finish_after_ledger_read)
            assert completed

            # Re-reading must also refresh an already-populated identity map.
            reader.refresh(batch.job)
            refreshed = json.loads(export_evaluation(get_evaluation(reader, batch_id), "json"))
            assert refreshed["job_status"] == "succeeded"
            assert refreshed["completed_run_count"] == 20
            assert all(row["candidate"] is not None and row["validation_report"] is not None for row in refreshed["runs"])
    finally:
        if job is not None:
            with engine.begin() as connection:
                connection.execute(delete(EvaluationBatch).where(EvaluationBatch.job_id == job.job_id))
        engine.dispose()
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")


def test_checkpoint_waiting_for_a_job_lock_cannot_outlive_its_lease(postgres_test_url):
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", "head")
    engine = create_engine(postgres_test_url, connect_args={
        "options": "-c lock_timeout=5000 -c statement_timeout=10000",
    })
    job = None
    contender = None
    try:
        with Session(engine) as db:
            job = claimed_evaluation(db)
            batch_id, _ = begin_evaluation(db, job.job_id, job.claim_token)
        waiting = threading.Event()
        errors = []

        def attempt_checkpoint():
            try:
                with Session(engine) as db:
                    checkpoint_evaluation_run(
                        db, job.job_id, job.claim_token, batch_id,
                        EvaluationRunRecord(0, EvaluationMode.GUIDED, 0, EvaluationStatus.RUNNING),
                    )
            except Exception as error:
                errors.append(error)

        def mark_lock_attempt(connection, cursor, statement, parameters, context, executemany):
            if threading.current_thread() is contender and "for update" in statement.lower():
                waiting.set()

        with engine.begin() as blocker:
            blocker.execute(update(ExecutionJob).where(ExecutionJob.job_id == job.job_id).values(
                lease_expires_at=func.clock_timestamp() + text("INTERVAL '0.1 second'"),
            ))
            contender = threading.Thread(target=attempt_checkpoint)
            event.listen(engine, "before_cursor_execute", mark_lock_attempt)
            try:
                contender.start()
                assert waiting.wait(5), "Checkpoint did not reach the locked job"
                blocker.execute(text("SELECT pg_sleep(0.2)"))
            finally:
                event.remove(engine, "before_cursor_execute", mark_lock_attempt)
        contender.join(10)
        assert not contender.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
        assert "claim" in str(errors[0])
        with Session(engine) as db:
            assert {run.status for run in get_evaluation(db, batch_id).runs} == {"pending"}
            recovery = JobRepository(db).recover_stale()
            db.commit()
            assert recovery.failed == 1 and recovery.requeued == 0
    finally:
        if contender is not None:
            contender.join(15)
        if job is not None:
            with engine.begin() as connection:
                connection.execute(delete(EvaluationBatch).where(EvaluationBatch.job_id == job.job_id))
        engine.dispose()
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")
