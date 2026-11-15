from hashlib import sha256
import json

import pytest
from sqlalchemy.orm import Session

from app.services.evaluation import (
    SEED_MAX,
    EvaluationMode,
    EvaluationStatus,
    export_evaluation,
    persist_evaluation,
    run_matched_evaluation,
)
from app.services.ga import GAConfig, GAEngine, HistoricalScoreBreakdown
from app.tests.support.scheduling import TERM_ID, seed_scheduling_problem
from app.tests.support.api import make_api_client
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider


pytestmark = pytest.mark.postgres
OUTCOME_HPA = 0.75


def fixed_historical_scores() -> HistoricalScoreBreakdown:
    return HistoricalScoreBreakdown(
        teacher_subject_score=OUTCOME_HPA,
        teacher_day_score=OUTCOME_HPA,
        teacher_time_score=OUTCOME_HPA,
        teacher_subject_room_score=OUTCOME_HPA,
        historical_pattern_attainment=OUTCOME_HPA,
    )


class FixedHistoricalScorer:
    def score(self, problem, assignments):
        return fixed_historical_scores()


def test_persisted_evaluation_exports_failed_and_successful_runs(db_session: Session):
    seed_scheduling_problem(db_session)
    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)
    config = GAConfig(
        population_size=12,
        max_generations=3,
        tournament_size=3,
        elite_count=1,
        stagnation_limit=None,
    )

    def runner(mode, run_config):
        if mode is EvaluationMode.UNGUIDED and run_config.random_seed == 9:
            raise RuntimeError("retained failure")
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(problem, run_config, historical_scorer=scorer).run()

    evaluation = run_matched_evaluation(
        problem,
        sha256(b"persisted fixture").hexdigest(),
        config,
        [SEED_MAX, *range(1, 10)],
        runner,
        lambda candidate: fixed_historical_scores(),
    )
    batch = persist_evaluation(db_session, evaluation)
    content = export_evaluation(batch).decode("utf-8-sig")
    response = make_api_client(db_session).get(
        f"/api/v1/evaluations/{batch.evaluation_batch_id}/export"
    )

    succeeded = [run for run in batch.runs if run.status == EvaluationStatus.SUCCEEDED.value]

    assert len(batch.runs) == 20
    assert sum(run.status == EvaluationStatus.FAILED.value for run in batch.runs) == 1
    assert {run.random_seed for run in batch.runs} == {SEED_MAX, *range(1, 10)}
    assert all(run.termination_generation is not None for run in succeeded)
    assert all(run.run_total_seconds >= run.elapsed_seconds for run in succeeded)
    assert all(run.generation_metrics for run in succeeded)
    assert all(run.candidate["term_id"] == str(TERM_ID) for run in succeeded)
    assert all(run.candidate["assignments"] for run in succeeded)
    assert all(
        run.validation_report["assigned_class_count"] == run.assigned_section_count
        and run.validation_report["hard_conflict_count"] == run.hard_conflict_count
        for run in succeeded
    )
    assert all(run.historical_pattern_attainment == OUTCOME_HPA for run in succeeded)
    assert all(
        run.historical_score_breakdown["teacher_subject_score"] == OUTCOME_HPA
        for run in succeeded
    )
    assert all(
        metric["best_fitness"][2] == 0.0
        for run in succeeded
        if run.mode == EvaluationMode.UNGUIDED.value
        for metric in run.generation_metrics
    )
    assert "termination_generation" in content.splitlines()[0]
    assert "generation_metrics" in content.splitlines()[0]
    assert "historical_score_breakdown" in content.splitlines()[0]
    assert content.splitlines()[0].endswith(",run_total_seconds")
    assert "retained failure" in content
    assert content.count("\n") == 21
    assert response.status_code == 200
    assert "retained failure" in response.content.decode("utf-8-sig")


def test_failed_contract_keeps_candidate_but_not_unverified_outcome_metrics(db_session: Session):
    from dataclasses import replace
    from app.services.evaluation import get_evaluation

    seed_scheduling_problem(db_session)
    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(TERM_ID)
    config = GAConfig(population_size=4, max_generations=1, tournament_size=2, elite_count=0)

    def runner(mode, run_config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        result = GAEngine(problem, run_config, historical_scorer=scorer).run()
        return replace(result, candidate=replace(result.candidate, assignments=()))

    evaluation = run_matched_evaluation(
        problem, sha256(b"mismatched fixture").hexdigest(), config, range(10),
        runner, lambda candidate: fixed_historical_scores(),
    )
    batch = persist_evaluation(db_session, evaluation)
    db_session.expire_all()
    batch = get_evaluation(db_session, batch.evaluation_batch_id)
    assert all(run.status == "failed" for run in batch.runs)
    assert all(run.candidate["assignments"] == [] for run in batch.runs)
    assert all(run.validation_report["assigned_class_count"] == 0 for run in batch.runs)
    assert all(run.assigned_section_count is None for run in batch.runs)
    assert all(run.hard_conflict_count is None for run in batch.runs)
    assert all(run.generation_metrics is None for run in batch.runs)
    assert all(run.historical_pattern_attainment is None for run in batch.runs)
    artifact = json.loads(export_evaluation(batch, "json"))
    assert all(run["status"] == "failed" for run in artifact["runs"])
    assert all(run["candidate"]["assignments"] == [] for run in artifact["runs"])
    assert all(run["validation_report"]["assigned_class_count"] == 0 for run in artifact["runs"])
    assert all(run["assigned_class_count"] is None for run in artifact["runs"])
