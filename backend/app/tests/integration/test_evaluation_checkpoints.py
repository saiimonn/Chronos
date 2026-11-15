"""CHR-134 / CHR-EVAL-011 checkpoint durability and claim fencing."""

from collections import Counter
from dataclasses import replace
import csv
import io
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from app.models import EvaluationBatch, ExecutionJob, JobType
from app.repositories.jobs import JobRepository
from app.services.evaluation import (
    EvaluationMode, EvaluationRunRecord, EvaluationStatus, get_evaluation,
    run_matched_evaluation,
)
from app.services.ga import GAEngine
from app.services.ga.rule_scoring import RuleBackedHistoricalScorer
from app.services.jobs.codecs import decode_ga_config, decode_guidance_snapshot, decode_scheduling_problem
from app.tests.integration.test_evaluation_jobs_api import SMALL_CONFIG, _admin, _rule_set
from app.tests.support.scheduling import TERM_ID, seed_scheduling_problem


pytestmark = pytest.mark.postgres


def claimed_evaluation(db):
    seed_scheduling_problem(db)
    rule_set = _rule_set(db)
    accepted = _admin(db).post("/api/v1/evaluations", json={
        "term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id),
        "config": SMALL_CONFIG,
    })
    assert accepted.status_code == 202
    job = JobRepository(db).claim_next(
        claimed_by="synthetic-evaluation-worker", lease_seconds=120,
        job_types=[JobType.EVALUATION],
    )
    db.commit()
    return job


def test_begin_retains_twenty_planned_slots_and_the_exact_input_snapshot(db_session):
    from app.services.evaluation import begin_evaluation

    job = claimed_evaluation(db_session)
    batch_id, snapshot = begin_evaluation(db_session, job.job_id, job.claim_token)
    db_session.expire_all()
    batch = get_evaluation(db_session, batch_id)
    assert snapshot == job.request == batch.input_snapshot
    assert batch.job_id == job.job_id
    assert len(batch.runs) == 20
    assert {run.status for run in batch.runs} == {"pending"}
    assert {(run.pair_index, run.mode, run.random_seed) for run in batch.runs} == {
        (index, mode, seed)
        for index, seed in enumerate(job.request["seeds"])
        for mode in ("guided", "unguided")
    }
    progress = JobRepository(db_session).get(job.job_id).progress
    assert progress["evaluation_batch_id"] == str(batch_id)
    assert progress["completed_run_count"] == 0
    with pytest.raises(ValueError, match="already"):
        begin_evaluation(db_session, job.job_id, job.claim_token)
    assert db_session.scalar(select(func.count()).select_from(EvaluationBatch)) == 1


def test_interruption_retains_completed_running_and_unstarted_slots(db_session):
    from app.services.evaluation import begin_evaluation, checkpoint_evaluation_run

    job = claimed_evaluation(db_session)
    batch_id, payload = begin_evaluation(db_session, job.job_id, job.claim_token)
    problem = decode_scheduling_problem(payload["problem"])
    scorer = RuleBackedHistoricalScorer(decode_guidance_snapshot(payload))

    def checkpoint(record):
        checkpoint_evaluation_run(db_session, job.job_id, job.claim_token, batch_id, record)

    def runner(mode, config):
        if mode is EvaluationMode.UNGUIDED:
            raise SystemExit("synthetic interruption")
        return GAEngine(problem, config, historical_scorer=scorer).run()

    with pytest.raises(SystemExit, match="synthetic interruption"):
        run_matched_evaluation(
            problem, payload["input_fingerprint"], decode_ga_config(payload["config"]),
            payload["seeds"], runner, lambda candidate: scorer.score(problem, candidate.assignments),
            checkpoint=checkpoint,
        )
    db_session.expire_all()
    batch = get_evaluation(db_session, batch_id)
    assert Counter(run.status for run in batch.runs) == {"succeeded": 1, "running": 1, "pending": 18}
    first = next(run for run in batch.runs if run.status == "succeeded")
    assert first.candidate["assignments"] and first.validation_report is not None
    assert JobRepository(db_session).get(job.job_id).progress["completed_run_count"] == 1
    before = first.candidate
    with pytest.raises(ValueError, match="finished"):
        checkpoint(EvaluationRunRecord(0, EvaluationMode.GUIDED, 0, EvaluationStatus.RUNNING))
    db_session.expire_all()
    assert next(run for run in get_evaluation(db_session, batch_id).runs if run.status == "succeeded").candidate == before


@pytest.mark.parametrize("expired", [False, True])
def test_invalid_claim_cannot_create_evaluation_evidence(db_session, expired):
    from app.services.evaluation import begin_evaluation

    job = claimed_evaluation(db_session)
    token = job.claim_token if expired else uuid4()
    if expired:
        db_session.execute(update(ExecutionJob).where(ExecutionJob.job_id == job.job_id).values(
            lease_expires_at=func.clock_timestamp() - text("INTERVAL '1 second'"),
        ))
        db_session.commit()
    with pytest.raises(ValueError, match="claim"):
        begin_evaluation(db_session, job.job_id, token)
    assert db_session.scalar(select(func.count()).select_from(EvaluationBatch)) == 0


def test_checkpoint_refuses_a_foreign_claim_or_changed_seed(db_session):
    from app.services.evaluation import begin_evaluation, checkpoint_evaluation_run

    job = claimed_evaluation(db_session)
    batch_id, _ = begin_evaluation(db_session, job.job_id, job.claim_token)
    record = EvaluationRunRecord(0, EvaluationMode.GUIDED, 0, EvaluationStatus.RUNNING)
    with pytest.raises(ValueError, match="claim"):
        checkpoint_evaluation_run(db_session, job.job_id, uuid4(), batch_id, record)
    with pytest.raises(ValueError, match="seed"):
        checkpoint_evaluation_run(db_session, job.job_id, job.claim_token, batch_id, replace(record, random_seed=999))
    db_session.expire_all()
    assert {run.status for run in get_evaluation(db_session, batch_id).runs} == {"pending"}


def test_live_exports_are_refused_and_terminal_exports_keep_unstarted_slots(db_session, tmp_path):
    from app.services.evaluation import begin_evaluation
    from app.tests.integration.test_evaluation_jobs_api import analysis
    from app.tests.support.api import make_api_client

    job = claimed_evaluation(db_session)
    batch_id, _ = begin_evaluation(db_session, job.job_id, job.claim_token)
    client = make_api_client(db_session, username="export-reader")
    path = f"/api/v1/evaluations/{batch_id}/export"
    for format in ("csv", "xlsx", "json"):
        response = client.get(path, params={"format": format})
        assert response.status_code == 409
        assert response.json()["code"] == "EVALUATION_RUNNING"
    JobRepository(db_session).timeout(job.job_id, job.claim_token)
    db_session.commit()
    response = client.get(path)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 20
    assert {row["status"] for row in rows} == {"failed"}
    assert {row["execution_state"] for row in rows} == {"pending"}
    assert all("not started" in row["error_message"] for row in rows)
    exported = tmp_path / "interrupted.csv"
    exported.write_bytes(response.content)
    assert len(analysis.load_evaluation_export(exported).runs) == 20
    artifact = client.get(path, params={"format": "json"})
    assert artifact.status_code == 200
    assert artifact.headers["Content-Type"].startswith("application/json")
    evidence = artifact.json()
    assert evidence["input_snapshot"] == job.request
    assert evidence["job_status"] == "timed_out"
    assert evidence["completed_run_count"] == 0
    assert len(evidence["runs"]) == 20
    assert {row["status"] for row in evidence["runs"]} == {"pending"}
    assert all(row["candidate"] is None for row in evidence["runs"])


def test_worker_uses_the_persisted_snapshot_and_refuses_duplicate_execution(db_session, monkeypatch):
    from contextlib import nullcontext
    from app.services.jobs.handlers import run_evaluation

    job = claimed_evaluation(db_session)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))
    payload = {
        "version": job.request["version"], "job_id": str(job.job_id),
        "claim_token": str(job.claim_token), "seeds": [999], "problem": {},
    }
    result = run_evaluation(payload)
    batch = get_evaluation(db_session, result["evaluation_batch_id"])
    assert batch.input_snapshot == job.request
    assert result["succeeded_run_count"] == 20
    assert {run.random_seed for run in batch.runs} == set(job.request["seeds"])
    with pytest.raises(ValueError, match="already"):
        run_evaluation(payload)
    assert db_session.scalar(select(func.count()).select_from(EvaluationBatch)) == 1


def test_lease_loss_before_commit_rolls_back_the_whole_checkpoint(db_session, monkeypatch):
    from app.services.evaluation import begin_evaluation, checkpoint_evaluation_run

    job = claimed_evaluation(db_session)
    batch_id, _ = begin_evaluation(db_session, job.job_id, job.claim_token)
    monkeypatch.setattr(JobRepository, "heartbeat", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="expired"):
        checkpoint_evaluation_run(
            db_session, job.job_id, job.claim_token, batch_id,
            EvaluationRunRecord(0, EvaluationMode.GUIDED, 0, EvaluationStatus.RUNNING),
        )
    db_session.expire_all()
    assert {run.status for run in get_evaluation(db_session, batch_id).runs} == {"pending"}
    assert JobRepository(db_session).get(job.job_id).progress["completed_run_count"] == 0


def test_stale_lease_recovery_preserves_the_batch_and_never_requeues_the_experiment(db_session):
    from app.services.evaluation import begin_evaluation, checkpoint_evaluation_run, export_evaluation

    job = claimed_evaluation(db_session)
    batch_id, _ = begin_evaluation(db_session, job.job_id, job.claim_token)
    checkpoint_evaluation_run(
        db_session, job.job_id, job.claim_token, batch_id,
        EvaluationRunRecord(0, EvaluationMode.GUIDED, 0, EvaluationStatus.RUNNING),
    )
    db_session.execute(update(ExecutionJob).where(ExecutionJob.job_id == job.job_id).values(
        lease_expires_at=func.clock_timestamp() - text("INTERVAL '1 second'"),
    ))
    db_session.commit()
    recovered = JobRepository(db_session).recover_stale()
    db_session.commit()
    assert recovered.failed == 1 and recovered.requeued == 0
    db_session.expire_all()
    batch = get_evaluation(db_session, batch_id)
    assert len(batch.runs) == 20
    assert Counter(run.status for run in batch.runs) == {"running": 1, "pending": 19}
    artifact = json.loads(export_evaluation(batch, "json"))
    assert artifact["job_status"] == "failed"
    assert artifact["job_error_code"] == "worker_lease_exhausted"
