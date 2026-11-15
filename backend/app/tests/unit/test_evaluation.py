import csv
from dataclasses import replace
from hashlib import sha256
import io
import json
from time import sleep
from uuid import UUID

import pytest

from app.services.evaluation import (
    EVALUATION_EXPORT_HEADERS,
    SEED_MAX,
    SEED_MIN,
    EvaluationMode,
    EvaluationStatus,
    export_evaluation,
    run_matched_evaluation,
)
from app.services.ga import GAConfig, GAEngine, HistoricalScoreBreakdown
from app.models.evaluation import EvaluationBatch, EvaluationRun
from app.tests.support.ga import make_ga_problem


TERM_ID = make_ga_problem().term.term_id
FINGERPRINT = sha256(b"CHR-38 fixture input").hexdigest()
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


def _config(**overrides) -> GAConfig:
    return GAConfig(
        population_size=12,
        max_generations=3,
        tournament_size=3,
        elite_count=1,
        stagnation_limit=None,
        **overrides,
    )


def test_run_total_time_covers_work_outside_the_engine_loop():
    setup_seconds = 0.02

    def runner(mode, config):
        sleep(setup_seconds)  # stands in for decoding and scorer setup
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(make_ga_problem(), config, historical_scorer=scorer).run()

    result = run_matched_evaluation(
        make_ga_problem(),
        FINGERPRINT,
        _config(),
        range(100, 110),
        runner,
        lambda candidate: fixed_historical_scores(),
    )

    for run in result.runs:
        assert run.status is EvaluationStatus.SUCCEEDED
        assert run.run_total_seconds >= run.result.metrics.elapsed_seconds + setup_seconds


def test_matched_pairs_share_input_config_and_seed():
    observed = []

    def runner(mode, config):
        observed.append((mode, config))
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(make_ga_problem(), config, historical_scorer=scorer).run()

    result = run_matched_evaluation(
        make_ga_problem(),
        FINGERPRINT,
        _config(),
        range(100, 110),
        runner,
        lambda candidate: fixed_historical_scores(),
    )

    assert len(result.runs) == 20
    assert result.input_fingerprint == FINGERPRINT
    for pair_index, seed in enumerate(range(100, 110)):
        pair = result.runs[pair_index * 2 : pair_index * 2 + 2]
        assert [run.mode for run in pair] == [EvaluationMode.GUIDED, EvaluationMode.UNGUIDED]
        assert [run.random_seed for run in pair] == [seed, seed]
        assert [run.result.metrics.config for run in pair] == [
            replace(result.config, random_seed=seed),
            replace(result.config, random_seed=seed),
        ]
        assert [run.historical_pattern_attainment for run in pair] == [
            OUTCOME_HPA,
            OUTCOME_HPA,
        ]
        assert pair[1].result.metrics.historical_pattern_attainment == 0.0
    assert observed[0][1].random_seed == 100


def test_failed_runs_are_retained_without_skipping_their_pairs():
    def runner(mode, config):
        if mode is EvaluationMode.GUIDED and config.random_seed == 104:
            raise RuntimeError("guided run failed")
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(make_ga_problem(), config, historical_scorer=scorer).run()

    result = run_matched_evaluation(
        make_ga_problem(),
        FINGERPRINT,
        _config(),
        range(100, 110),
        runner,
        lambda candidate: fixed_historical_scores(),
    )

    failed = [run for run in result.runs if run.status is EvaluationStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].pair_index == 4
    assert failed[0].mode is EvaluationMode.GUIDED
    assert failed[0].error_message == "guided run failed"
    assert len(result.runs) == 20


def test_runner_contract_violations_are_retained_as_failures():
    def runner(mode, config):
        if mode is EvaluationMode.GUIDED and config.random_seed == 100:
            config = replace(config, random_seed=999)
        use_guidance = mode is EvaluationMode.GUIDED or config.random_seed == 101
        scorer = FixedHistoricalScorer() if use_guidance else None
        return GAEngine(make_ga_problem(), config, historical_scorer=scorer).run()

    result = run_matched_evaluation(
        make_ga_problem(),
        FINGERPRINT,
        _config(),
        range(100, 110),
        runner,
        lambda candidate: fixed_historical_scores(),
    )

    failures = [run for run in result.runs if run.status is EvaluationStatus.FAILED]
    assert [(run.mode, run.random_seed) for run in failures] == [
        (EvaluationMode.GUIDED, 100),
        (EvaluationMode.UNGUIDED, 101),
    ]
    assert "different seed or configuration" in failures[0].error_message
    assert "must not use historical guidance" in failures[1].error_message


@pytest.mark.parametrize("seeds", [range(9), [1] * 10])
def test_matched_evaluation_requires_ten_unique_seeds(seeds):
    with pytest.raises(ValueError):
        run_matched_evaluation(
            make_ga_problem(),
            FINGERPRINT,
            _config(),
            seeds,
            lambda *_: None,
            lambda candidate: fixed_historical_scores(),
        )


@pytest.mark.parametrize("out_of_range", [SEED_MAX + 1, SEED_MIN - 1])
def test_matched_evaluation_rejects_seeds_wider_than_64_bits(out_of_range):
    with pytest.raises(ValueError, match="64-bit"):
        run_matched_evaluation(
            make_ga_problem(),
            FINGERPRINT,
            _config(),
            [*range(9), out_of_range],
            lambda *_: None,
            lambda candidate: fixed_historical_scores(),
        )



@pytest.mark.parametrize("out_of_range", [SEED_MAX + 1, SEED_MIN - 1])
def test_matched_evaluation_rejects_a_base_config_seed_wider_than_64_bits(out_of_range):
    """The base config is exported with the batch, so its seed must load too."""
    with pytest.raises(ValueError, match="64-bit"):
        run_matched_evaluation(
            make_ga_problem(),
            FINGERPRINT,
            replace(_config(), random_seed=out_of_range),
            list(range(10)),
            lambda *_: None,
            lambda candidate: fixed_historical_scores(),
        )

def test_matched_evaluation_requires_a_sha256_input_fingerprint():
    with pytest.raises(ValueError):
        run_matched_evaluation(
            make_ga_problem(),
            "not-a-fingerprint",
            _config(),
            range(10),
            lambda *_: None,
            lambda candidate: fixed_historical_scores(),
        )


def test_export_keeps_failed_runs_and_reports_metrics():
    batch = EvaluationBatch(
        term_id=TERM_ID,
        input_fingerprint=FINGERPRINT,
        config={"random_seed": 0},
        runs=[
            EvaluationRun(
                pair_index=0,
                mode="guided",
                random_seed=100,
                status="succeeded",
                termination_reason="OPTIMAL_REACHED",
                termination_generation=7,
                elapsed_seconds=1.5,
                required_section_count=4,
                assigned_section_count=3,
                hard_conflict_count=1,
                historical_pattern_attainment=0.75,
                historical_score_breakdown={
                    "teacher_subject_score": 0.75,
                    "teacher_day_score": 0.75,
                    "teacher_time_score": 0.75,
                    "teacher_subject_room_score": 0.75,
                    "historical_pattern_attainment": 0.75,
                },
                generation_metrics=[
                    {
                        "generation": 0,
                        "best_fitness": [3.0, -1.0, 0.75],
                        "mean_assignment_completeness": 0.5,
                    }
                ],
            ),
            EvaluationRun(
                pair_index=0,
                mode="unguided",
                random_seed=100,
                status="failed",
                error_message="worker stopped",
            ),
        ],
    )

    content = export_evaluation(batch).decode("utf-8-sig")
    rows = content.splitlines()
    parsed = list(csv.DictReader(io.StringIO(content)))

    assert rows[0].split(",") == list(EVALUATION_EXPORT_HEADERS)
    assert "guided,100,succeeded,OPTIMAL_REACHED,7,1.5,4,3,0.75,1,0.75" in rows[1]
    assert "unguided,100,failed" in rows[2]
    assert "worker stopped" in rows[2]
    assert json.loads(parsed[0]["generation_metrics"])[0]["best_fitness"] == [
        3.0,
        -1.0,
        0.75,
    ]
    assert json.loads(parsed[0]["historical_score_breakdown"])[
        "teacher_subject_room_score"
    ] == 0.75
    assert parsed[1]["generation_metrics"] == ""
    assert parsed[1]["historical_score_breakdown"] == ""


def test_empty_candidates_cannot_reuse_complete_optimizer_metrics():
    problem = make_ga_problem(include_unstaffable=False)

    def runner(mode, config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        result = GAEngine(problem, config, historical_scorer=scorer).run()
        return replace(result, candidate=replace(result.candidate, assignments=()))

    evaluation = run_matched_evaluation(
        problem, FINGERPRINT, _config(), range(10), runner,
        lambda candidate: fixed_historical_scores(),
    )
    assert all(run.status is EvaluationStatus.FAILED for run in evaluation.runs)
    assert all(run.result.candidate.assignments == () for run in evaluation.runs)
    assert all(run.validation_report.assigned_class_count == 0 for run in evaluation.runs)
    assert all("validation" in run.error_message for run in evaluation.runs)


@pytest.mark.parametrize("field", ["required_class_count", "assigned_class_count", "hard_conflict_count"])
def test_fresh_validation_rejects_inconsistent_optimizer_counters(field):
    problem = make_ga_problem()

    def runner(mode, config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        result = GAEngine(problem, config, historical_scorer=scorer).run()
        return replace(result, metrics=replace(result.metrics, **{field: getattr(result.metrics, field) + 1}))

    evaluation = run_matched_evaluation(
        problem, FINGERPRINT, _config(), range(10), runner,
        lambda candidate: fixed_historical_scores(),
    )
    assert all(run.status is EvaluationStatus.FAILED for run in evaluation.runs)
    assert all(run.result is not None and run.validation_report is not None for run in evaluation.runs)


def test_wrong_term_candidates_fail_but_remain_available_for_audit():
    problem = make_ga_problem()

    def runner(mode, config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        result = GAEngine(problem, config, historical_scorer=scorer).run()
        return replace(result, candidate=replace(result.candidate, term_id=UUID(int=134)))

    evaluation = run_matched_evaluation(
        problem, FINGERPRINT, _config(), range(10), runner,
        lambda candidate: fixed_historical_scores(),
    )
    assert all(run.status is EvaluationStatus.FAILED for run in evaluation.runs)
    assert all(run.result.candidate.term_id == UUID(int=134) for run in evaluation.runs)
    assert all(run.validation_report is None for run in evaluation.runs)


def test_independently_validated_infeasible_runs_are_not_discarded():
    problem = make_ga_problem(include_unstaffable=True)

    def runner(mode, config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(problem, config, historical_scorer=scorer).run()

    evaluation = run_matched_evaluation(
        problem, FINGERPRINT, _config(), range(10), runner,
        lambda candidate: fixed_historical_scores(),
    )
    assert all(run.status is EvaluationStatus.SUCCEEDED for run in evaluation.runs)
    assert all(not run.validation_report.is_export_eligible for run in evaluation.runs)
    assert all(run.validation_report.assigned_class_count == run.result.metrics.assigned_class_count for run in evaluation.runs)


def test_checkpoints_mark_each_run_started_before_execution_and_finished_after_validation():
    problem = make_ga_problem()
    checkpoints = []

    def runner(mode, config):
        assert checkpoints[-1].status is EvaluationStatus.RUNNING
        assert checkpoints[-1].mode is mode
        assert checkpoints[-1].random_seed == config.random_seed
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(problem, config, historical_scorer=scorer).run()

    result = run_matched_evaluation(
        problem, FINGERPRINT, _config(), range(10), runner,
        lambda candidate: fixed_historical_scores(), checkpoint=checkpoints.append,
    )
    assert len(checkpoints) == 40
    assert checkpoints[1::2] == list(result.runs)
    assert all(record.validation_report is not None for record in checkpoints[1::2])


def test_checkpoint_failure_stops_execution_instead_of_losing_evidence_silently():
    calls = []

    def runner(*args):
        calls.append(args)

    def unavailable(_record):
        raise RuntimeError("checkpoint unavailable")

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        run_matched_evaluation(
            make_ga_problem(), FINGERPRINT, _config(), range(10), runner,
            lambda candidate: fixed_historical_scores(), checkpoint=unavailable,
        )
    assert calls == []
