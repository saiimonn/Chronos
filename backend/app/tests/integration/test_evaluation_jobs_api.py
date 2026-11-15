"""CHR-115 / CHR-EVAL-009 matched evaluation through the local worker."""

from contextlib import nullcontext
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvaluationBatch, ExecutionJob, HistoricalSnapshot, Term, UserRole
from app.models.extracted_rule import ExtractedRule
from app.models.rule_set import RuleSet
from app.repositories.jobs import JobRepository
from app.services.jobs.handlers import dispatch
from app.tests.support.api import make_api_client
from app.tests.support.history import seed_historical_terms
from app.tests.support.scheduling import FACULTY_ID, TERM_ID, seed_scheduling_problem

pytestmark = pytest.mark.postgres

SCRIPT = Path(__file__).parents[3] / "scripts" / "colab_prototype" / "analyze_matched_ga.py"
SPEC = importlib.util.spec_from_file_location("colab_matched_ga_chr115", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)

SMALL_CONFIG = {
    "population_size": 12,
    "max_generations": 3,
    "tournament_size": 3,
    "elite_count": 1,
    "stagnation_limit": None,
}


def _admin(db: Session):
    return make_api_client(db, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="admin")


def _rule_set(db: Session, status: str = "draft") -> RuleSet:
    rule_set = RuleSet(rule_set_id=uuid4(), version=7, status=status)
    db.add(rule_set)
    db.flush()
    db.add(
        ExtractedRule(
            rule_id=uuid4(),
            rule_set_id=rule_set.rule_set_id,
            pattern_type="teacher_subject",
            faculty_id=FACULTY_ID,
            subject_code="<SUBJECT-1>",
            support=Decimal("0.5000"),
            confidence=Decimal("0.7500"),
            lift=Decimal("1.2000"),
            term_recurrence=3,
            guidance_score=Decimal("0.8000"),
        )
    )
    db.flush()
    return rule_set


def _batch_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(EvaluationBatch))


def _run_claimed(db: Session, job):
    claim = JobRepository(db).claim_next(
        claimed_by="synthetic-evaluation-worker", lease_seconds=120,
        job_types=["evaluation"],
    )
    db.commit()
    assert claim.job_id == job.job_id
    result = dispatch(job.job_type, {
        **job.request, "job_id": str(claim.job_id), "claim_token": str(claim.claim_token),
    })
    assert JobRepository(db).complete(claim.job_id, claim.claim_token, result=result) is not None
    db.commit()
    return result


def test_submission_snapshots_inputs_and_queues_without_running(db_session: Session):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)
    before = _batch_count(db_session)

    response = _admin(db_session).post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "evaluation"
    assert body["status"] == "queued"
    assert response.headers["Location"] == f"/api/v1/jobs/{body['job_id']}"
    job = db_session.get(ExecutionJob, body["job_id"])
    assert job.max_attempts == 1
    assert job.request["seeds"] == list(range(10))
    assert job.request["historical_guidance"]["rule_set_id"] == str(rule_set.rule_set_id)
    assert job.request["historical_guidance"]["rule_set_version"] == 7
    assert len(job.request["historical_guidance"]["rules"]) == 1
    assert job.request["problem"]["term"]["term_id"] == str(TERM_ID)
    assert len(job.request["input_fingerprint"]) == 64
    # Nothing runs in the API process.
    assert _batch_count(db_session) == before


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"seeds": list(range(9))}, 422),
        ({"seeds": [1] * 10}, 422),
        ({"seeds": [2**63, *range(9)]}, 422),
        ({"config": {"crossover_rate": 2}}, 422),
        ({"config": {"random_seed": 2**63}}, 422),
        ({"config": {"random_seed": -(2**63) - 1}}, 422),
        ({"term_id": str(uuid4())}, 404),
        ({"rule_set_id": str(uuid4())}, 404),
    ],
)
def test_submission_refuses_invalid_requests(db_session: Session, change: dict, status: int):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)

    response = _admin(db_session).post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id), **change},
    )

    assert response.status_code == status


def test_only_administrators_can_submit(db_session: Session):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)
    client = make_api_client(db_session, role=UserRole.SCHEDULING_COMMITTEE)

    response = client.post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("outcome", ["failed", "timed_out"])
def test_unsuccessful_job_status_exposes_only_the_batch_reference(
    db_session: Session, outcome: str
):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)
    client = _admin(db_session)
    location = client.post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    ).headers["Location"]
    repository = JobRepository(db_session)
    claim = repository.claim_next(
        claimed_by="synthetic-evaluation-worker", lease_seconds=120,
        job_types=["evaluation"],
    )
    batch_id = uuid4()
    repository.heartbeat(claim.job_id, claim.claim_token, lease_seconds=120, progress={
        "evaluation_batch_id": str(batch_id), "completed_run_count": 7,
        "planned_run_count": 20, "internal_path": "/tmp/evidence",
    })
    if outcome == "failed":
        repository.fail(claim.job_id, claim.claim_token, error_code="execution_failed",
                        error_message="Execution failed.")
    else:
        repository.timeout(claim.job_id, claim.claim_token)
    db_session.commit()

    body = client.get(location).json()

    assert body["status"] == outcome
    assert body["result"] is None
    assert body["evaluation_batch"] == {
        "evaluation_batch_id": str(batch_id), "completed_run_count": 7,
        "planned_run_count": 20,
    }
    assert "progress" not in body


def test_ten_pair_worker_run_exports_to_the_analysis_loader(
    db_session: Session, monkeypatch, tmp_path: Path
):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)
    client = _admin(db_session)
    seeds = [2**63 - 1, *range(1, 10)]
    accepted = client.post(
        "/api/v1/evaluations",
        json={
            "term_id": str(TERM_ID),
            "rule_set_id": str(rule_set.rule_set_id),
            "seeds": seeds,
            "config": SMALL_CONFIG,
        },
    )
    job = db_session.get(ExecutionJob, accepted.json()["job_id"])
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))

    # What the worker's child process runs for this job.
    result = _run_claimed(db_session, job)

    assert result["succeeded_run_count"] == 20
    assert result["failed_run_count"] == 0
    assert result["rule_set_id"] == str(rule_set.rule_set_id)
    batch = db_session.get(EvaluationBatch, result["evaluation_batch_id"])
    assert batch.job_id == job.job_id
    assert batch.input_snapshot == job.request
    assert batch.input_fingerprint == job.request["input_fingerprint"]
    assert len(batch.runs) == 20
    for pair_index, seed in enumerate(seeds):
        pair = [run for run in batch.runs if run.pair_index == pair_index]
        assert {run.mode for run in pair} == {"guided", "unguided"}
        assert {run.random_seed for run in pair} == {seed}

    export = client.get(f"/api/v1/evaluations/{batch.evaluation_batch_id}/export")
    assert export.status_code == 200
    path = tmp_path / "evaluation.csv"
    path.write_bytes(export.content)
    loaded = analysis.load_evaluation_export(path)

    assert len(loaded.runs) == 20
    assert set(loaded.runs["random_seed"]) == set(seeds)

    from uuid import UUID
    from app.domain import Assignment, CandidateSchedule
    from app.services.ga.rule_scoring import RuleBackedHistoricalScorer
    from app.services.jobs.codecs import decode_guidance_snapshot, decode_scheduling_problem
    from app.services.validation import ScheduleValidator

    evidence_response = client.get(f"/api/v1/evaluations/{batch.evaluation_batch_id}/export?format=json")
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()
    assert evidence["completed_run_count"] == 20
    frozen_problem = decode_scheduling_problem(evidence["input_snapshot"]["problem"])
    frozen_scorer = RuleBackedHistoricalScorer(decode_guidance_snapshot(evidence["input_snapshot"]))
    for run in evidence["runs"]:
        candidate = CandidateSchedule(
            term_id=UUID(run["candidate"]["term_id"]),
            assignments=tuple(Assignment(
                class_id=UUID(item["class_id"]), faculty_id=UUID(item["faculty_id"]),
                time_slot_id=UUID(item["time_slot_id"]), room_code=item["room_code"],
                meeting_days=item["meeting_days"],
            ) for item in run["candidate"]["assignments"]),
        )
        validation = ScheduleValidator().validate(frozen_problem, candidate)
        assert validation.assigned_class_count == run["assigned_class_count"]
        assert validation.hard_conflict_count == run["hard_conflict_count"]
        assert validation.is_export_eligible == run["validation_report"]["is_export_eligible"]
        score = frozen_scorer.score(frozen_problem, candidate.assignments)
        assert score.historical_pattern_attainment == pytest.approx(run["historical_pattern_attainment"])


@pytest.mark.parametrize("base_seed", [2**63 - 1, -(2**63)])
def test_base_config_seed_at_the_64_bit_limits_exports_to_the_analysis_loader(
    db_session: Session, monkeypatch, tmp_path: Path, base_seed: int
):
    seed_scheduling_problem(db_session)
    rule_set = _rule_set(db_session)
    client = _admin(db_session)
    accepted = client.post(
        "/api/v1/evaluations",
        json={
            "term_id": str(TERM_ID),
            "rule_set_id": str(rule_set.rule_set_id),
            "config": {**SMALL_CONFIG, "random_seed": base_seed},
        },
    )
    assert accepted.status_code == 202
    job = db_session.get(ExecutionJob, accepted.json()["job_id"])
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))

    result = _run_claimed(db_session, job)

    export = client.get(f"/api/v1/evaluations/{result['evaluation_batch_id']}/export")
    path = tmp_path / "evaluation.csv"
    path.write_bytes(export.content)
    loaded = analysis.load_evaluation_export(path)
    assert loaded.config["random_seed"] == base_seed


# CHR-136 / CHR-EVAL-012: the latest complete academic year is the holdout
# (thesis p.52), so 2024-2025 is withheld and 2023-2024 is training history.
HOLDOUT_GUARD_PERIODS = tuple(
    (year, number) for year in ("2023-2024", "2024-2025") for number in (1, 2, 3)
)


def _include_terms(db: Session, rule_set: RuleSet, periods) -> None:
    for year, number in periods:
        rule_set.included_terms.append(
            db.scalars(
                select(Term).where(Term.academic_year == year, Term.term_number == number)
            ).one()
        )
    db.flush()


def _evaluation_job_count(db: Session) -> int:
    return db.scalar(
        select(func.count())
        .select_from(ExecutionJob)
        .where(ExecutionJob.job_type == "evaluation")
    )


def test_rule_set_mined_from_holdout_terms_is_refused(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS)
    jobs_before = _evaluation_job_count(db_session)

    response = _admin(db_session).post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RULE_SET_INCLUDES_HOLDOUT"
    assert response.json()["details"]["withheld_periods"] == [
        "2024-2025 T1",
        "2024-2025 T2",
        "2024-2025 T3",
    ]
    assert _evaluation_job_count(db_session) == jobs_before


def test_rule_set_mined_from_training_terms_only_is_accepted(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[:3])

    response = _admin(db_session).post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    )

    assert response.status_code == 202


def _submit(db: Session, rule_set: RuleSet):
    return _admin(db).post(
        "/api/v1/evaluations",
        json={"term_id": str(TERM_ID), "rule_set_id": str(rule_set.rule_set_id)},
    )


def test_holdout_source_snapshot_is_refused(db_session: Session):
    seed_scheduling_problem(db_session)
    snapshot_ids = seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    rule_set = _rule_set(db_session)
    rule_set.source_snapshots.append(db_session.get(HistoricalSnapshot, snapshot_ids[-1]))
    db_session.flush()

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["details"]["withheld_periods"] == ["2024-2025 T3"]


def test_renaming_a_holdout_term_does_not_hide_its_snapshot_period(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[3:4])
    renamed = rule_set.included_terms[0]
    renamed.academic_year = "2019-2020"
    db_session.flush()

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["details"]["withheld_periods"] == ["2024-2025 T1"]


def test_unparsable_stored_year_does_not_break_submission(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    db_session.add(
        HistoricalSnapshot(source_term_id=uuid4(), academic_year="2024-2026", term_number=1)
    )
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[:3])

    assert _submit(db_session, rule_set).status_code == 202


def test_rule_set_without_provenance_is_refused_once_a_holdout_exists(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    rule_set = _rule_set(db_session)

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["code"] == "RULE_SET_PROVENANCE_UNKNOWN"


def test_terms_newer_than_the_holdout_year_are_refused_too(db_session: Session):
    # Evaluation-mode mining withholds newer terms as well as the holdout.
    newer = (("2025-2026", 1),)
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS + newer)
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[:3] + newer)

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["code"] == "RULE_SET_INCLUDES_HOLDOUT"
    assert response.json()["details"]["withheld_periods"] == ["2025-2026 T1"]


def test_newer_linked_term_without_a_snapshot_is_refused(db_session: Session):
    # An imported package links Terms by period; one may never have been captured.
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    db_session.add(Term(academic_year="2025-2026", term_number=1, is_active=False))
    db_session.flush()
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[:3] + (("2025-2026", 1),))

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["details"]["withheld_periods"] == ["2025-2026 T1"]


def test_linked_term_renamed_to_an_unreadable_year_is_refused(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_historical_terms(db_session, HOLDOUT_GUARD_PERIODS)
    db_session.add(Term(academic_year="2025-2026", term_number=1, is_active=False))
    db_session.flush()
    rule_set = _rule_set(db_session)
    _include_terms(db_session, rule_set, HOLDOUT_GUARD_PERIODS[:3] + (("2025-2026", 1),))
    rule_set.included_terms[-1].academic_year = "2025-26"
    db_session.flush()

    response = _submit(db_session, rule_set)

    assert response.status_code == 409
    assert response.json()["code"] == "RULE_SET_PROVENANCE_UNKNOWN"
