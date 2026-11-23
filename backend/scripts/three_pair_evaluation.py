"""Adviser-requested test: 3 matched pairs (seeds 0-2) for each of T1, T2, T3.

Same terms, Rule Set and GA configs as the official 10-pair runs; only the seed count differs.
Runs on the current code, so results are not comparable with the official batches.

From backend/, with a worker running under CHRONOS_MATCHED_PAIR_COUNT=3:

    CHRONOS_MATCHED_PAIR_COUNT=3 THREE_PAIR_OUT=<dir> PYTHONPATH=. python scripts/three_pair_evaluation.py

Submits one term at a time and waits. Skips a term whose <dir>/<term>/evaluation.csv exists.
"""

import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import AppUser, ExecutionJob
from app.repositories.jobs import JobRepository
from app.services import evaluation as service
from app.services.ga import GAConfig

RULE_SET_ID = "93acc4e1-8240-4ef8-acc5-950993ffbe31"
SEEDS = [0, 1, 2]
BASE = {"population_size": 50, "max_generations": 100, "crossover_rate": 0.8, "mutation_rate": 0.2,
        "gene_mutation_rate": 0.1, "tournament_size": 3, "elite_count": 1, "stagnation_limit": 20,
        "meeting_day_patterns": ["MW", "TTh"]}
TERMS = {
    "t1": ("95770b7b-efe8-4990-bdc0-ae3c0a1e8cba", BASE),
    "t2": ("7148f3f6-5dfa-4245-8322-38c31754f151", BASE),
    "t3": ("17c2d5e2-800b-4476-add2-7f5fd18d9583", {**BASE, "meeting_day_patterns": ["MTWThF"]}),
}
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def status_of(job):
    return getattr(job.status, "value", job.status)


def run_term(out_root, name, term_id, config):
    out = out_root / name
    if (out / "evaluation.csv").exists():
        print(f"{name}: already exported, skipping", flush=True)
        return
    out.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        admin = db.scalar(select(AppUser).where(AppUser.username == "admin"))
        job = service.submit_matched_evaluation(
            db, JobRepository(db), term_id=term_id, rule_set_id=RULE_SET_ID, seeds=SEEDS,
            config=GAConfig(**config), requested_by=admin.user_id,
            timeout_seconds=settings.job_evaluation_timeout_seconds)
        job_id = job.job_id
    record = {"term": name, "term_id": term_id, "job_id": str(job_id), "rule_set_id": RULE_SET_ID,
              "seeds": SEEDS, "config": config, "submitted_at_unix": time.time()}
    (out / "submission.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"{name}: submitted job {job_id}", flush=True)
    while True:
        time.sleep(20)
        with SessionLocal() as db:
            status = status_of(db.get(ExecutionJob, job_id))
        print(time.strftime("%H:%M:%S"), name, status, flush=True)
        if status in TERMINAL:
            break
    with SessionLocal() as db:
        batch = db.scalar(select(service.EvaluationBatch).where(service.EvaluationBatch.job_id == job_id))
        if batch is None:
            sys.exit(f"{name}: job ended {status} with no batch")
        for fmt in ("csv", "json"):
            (out / f"evaluation.{fmt}").write_bytes(service.export_evaluation(batch, fmt))
        record.update({"final_status": status, "evaluation_batch_id": str(batch.evaluation_batch_id),
                       "finished_at_unix": time.time()})
    (out / "submission.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"{name}: done, {status}", flush=True)


if __name__ == "__main__":
    if os.environ.get("CHRONOS_MATCHED_PAIR_COUNT") != "3":
        sys.exit("set CHRONOS_MATCHED_PAIR_COUNT=3")
    out_root = Path(os.environ["THREE_PAIR_OUT"])
    for name, (term_id, config) in TERMS.items():
        run_term(out_root, name, term_id, config)
