"""CHR-101 / CHR-COLAB-005 matched GA analysis tests."""

import csv
import importlib.util
import json
import math
from pathlib import Path
import sys

import pandas as pd
import pytest


SCRIPT = (
    Path(__file__).parents[3]
    / "scripts"
    / "colab_prototype"
    / "analyze_matched_ga.py"
)
SPEC = importlib.util.spec_from_file_location("colab_matched_ga", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)


def generation_metrics(
    final_assigned: int,
    final_conflicts: int,
    outcome_hpa: float,
    mode: str,
    generations: int = 2,
):
    return [
        {
            "generation": generation,
            "best_fitness": [
                # Strictly improving, so no stagnation stop before the end.
                float(max(0, final_assigned - (generations - generation))),
                float(-final_conflicts if generation == generations else -(final_conflicts + 1)),
                (
                    outcome_hpa
                    if mode == "guided" and generation == generations
                    else max(0.0, outcome_hpa - 0.1)
                    if mode == "guided"
                    else 0.0
                ),
            ],
            "mean_assignment_completeness": 0.5 + generation * 0.2,
        }
        for generation in range(generations + 1)
    ]


def evaluation_config(population_size: int = 20) -> dict[str, object]:
    return {
        "population_size": population_size,
        "max_generations": 2,
        "crossover_rate": 0.8,
        "mutation_rate": 0.2,
        "gene_mutation_rate": 0.1,
        "tournament_size": 3,
        "elite_count": 1,
        "stagnation_limit": 1,
        "random_seed": 0,
        "meeting_day_patterns": ["MWF", "TTh"],
    }


def historical_score_breakdown(hpa: float) -> dict[str, float]:
    return {
        "teacher_subject_score": hpa,
        "teacher_day_score": hpa,
        "teacher_time_score": hpa,
        "teacher_subject_room_score": hpa,
        "historical_pattern_attainment": hpa,
    }


def evaluation_rows() -> list[dict[str, object]]:
    rows = []
    config = json.dumps(
        evaluation_config(),
        sort_keys=True,
        separators=(",", ":"),
    )
    for pair_index in range(10):
        for mode in analysis.MODES:
            if pair_index == 4 and mode == "guided":
                rows.append(
                    {
                        "pair_index": pair_index,
                        "mode": mode,
                        "random_seed": 100 + pair_index,
                        "status": "failed",
                        "error_message": "synthetic retained failure",
                        "input_fingerprint": "a" * 64,
                        "config": config,
                    }
                )
                continue
            conflicts = int(pair_index == 5 and mode == "unguided")
            hpa = (0.6 if mode == "guided" else 0.4) + pair_index / 100
            # Pair 0 guided stops on stagnation after one generation, which
            # exercises carrying its terminal value forward to generation 2.
            stagnated = pair_index == 0 and mode == "guided"
            generations = 1 if stagnated else 2
            assigned = 4 if conflicts == 0 else 3
            history = generation_metrics(assigned, conflicts, hpa, mode, generations)
            if stagnated:
                history[0]["best_fitness"] = list(history[1]["best_fitness"])
            rows.append(
                {
                    "pair_index": pair_index,
                    "mode": mode,
                    "random_seed": 100 + pair_index,
                    "status": "succeeded",
                    "termination_reason": (
                        "STAGNATION" if stagnated else "MAX_GENERATIONS"
                    ),
                    "termination_generation": generations,
                    "elapsed_seconds": 1.0 + pair_index / 10,
                    "required_section_count": 4,
                    "assigned_section_count": assigned,
                    "assignment_completeness": assigned / 4,
                    "hard_conflict_count": conflicts,
                    "historical_pattern_attainment": hpa,
                    "historical_score_breakdown": json.dumps(
                        historical_score_breakdown(hpa),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "generation_metrics": json.dumps(
                        history,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "error_message": "",
                    "input_fingerprint": "a" * 64,
                    "config": config,
                }
            )
    return rows


def write_export(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=analysis.REQUIRED_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def write_generation_manifest(
    path: Path, evaluation_path: Path, rows: list[dict[str, object]]
) -> None:
    manifest = {
        "schema_version": 1,
        "traceability": "CHR-38 / CHR-EVAL-001",
        "evaluation_csv_sha256": analysis._sha256(evaluation_path),
        "input_fingerprint": rows[0]["input_fingerprint"],
        "generator_commit": "c" * 40,
        "generated_at_utc": "2026-10-03T00:00:00+00:00",
        "environment": {
            "python": "3.12.synthetic",
            "platform": "synthetic-linux",
            "processor": "synthetic-cpu",
        },
        "execution": {
            "concurrent_runs": 1,
            "shared_runtime": True,
            "run_order": [
                {
                    "pair_index": row["pair_index"],
                    "mode": row["mode"],
                    "random_seed": row["random_seed"],
                }
                for row in sorted(rows, key=lambda row: (row["pair_index"], row["mode"]))
            ],
        },
        "scoring": {
            "implementation": "synthetic.FixedHistoricalScorer",
            "rule_set_id": "00000000-0000-0000-0000-000000000001",
            "rule_set_sha256": "d" * 64,
            "weights": {
                "teacher_subject": 0.25,
                "teacher_day": 0.25,
                "teacher_time": 0.25,
                "teacher_subject_room": 0.25,
            },
        },
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def add_unguided_guidance(rows: list[dict[str, object]]) -> None:
    history = json.loads(str(rows[1]["generation_metrics"]))
    history[0]["best_fitness"][2] = 0.1
    rows[1]["generation_metrics"] = json.dumps(history)


def plateau_then_improve(rows: list[dict[str, object]]) -> None:
    # Pair 1 guided stagnates at generation 1 (stagnation_limit=1), so the
    # engine stops there; a generation-2 improvement cannot exist.
    history = json.loads(str(rows[2]["generation_metrics"]))
    for item, attainment in zip(history, (0.5, 0.5, 0.61), strict=True):
        item["best_fitness"] = [4.0, 0.0, attainment]
    rows[2]["generation_metrics"] = json.dumps(history)


def plateau_at_max_generations(rows: list[dict[str, object]]) -> None:
    # Pair 1 guided first stagnates at generation 2 == max_generations.
    history = json.loads(str(rows[2]["generation_metrics"]))
    for item, fitness in zip(
        history, ([3.0, -1.0, 0.5], [4.0, 0.0, 0.61], [4.0, 0.0, 0.61]), strict=True
    ):
        item["best_fitness"] = fitness
    rows[2]["generation_metrics"] = json.dumps(history)


def test_analysis_retains_failures_and_builds_requested_outputs(tmp_path: Path):
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    output = tmp_path / "analysis"
    rows = evaluation_rows()
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)

    manifest = analysis.analyze(source, generation_manifest, output)

    expected = {
        "validated_runs.csv",
        "mode_summary.csv",
        "descriptive_statistics.csv",
        "matched_seed_differences.csv",
        "convergence_by_run.csv",
        "convergence_summary.csv",
        "metric_boxplots.svg",
        "matched_seed_hpa.svg",
        "fitness_convergence.svg",
        "analysis_manifest.json",
    }
    assert {path.name for path in output.iterdir()} == expected
    assert all((output / name).read_text().lstrip().startswith("<") for name in expected if name.endswith(".svg"))

    runs = pd.read_csv(output / "validated_runs.csv")
    modes = pd.read_csv(output / "mode_summary.csv").set_index("mode")
    descriptions = pd.read_csv(output / "descriptive_statistics.csv")
    pairs = pd.read_csv(output / "matched_seed_differences.csv")
    convergence = pd.read_csv(output / "convergence_by_run.csv")

    assert len(runs) == 20
    assert runs["status"].value_counts().to_dict() == {"succeeded": 19, "failed": 1}
    assert json.loads(runs.loc[0, "historical_score_breakdown"])[
        "teacher_subject_score"
    ] == pytest.approx(0.6)
    assert modes.loc["guided", "failed_runs"] == 1
    assert modes.loc["guided", "feasible_run_rate"] == pytest.approx(0.9)
    assert modes.loc["unguided", "feasible_run_rate"] == pytest.approx(0.9)
    assert descriptions.query(
        "mode == 'guided' and metric == 'historical_pattern_attainment'"
    ).iloc[0]["count"] == 9
    assert len(pairs) == 10
    assert not bool(pairs.loc[pairs["pair_index"] == 4, "comparable"].iloc[0])
    assert math.isnan(pairs.loc[pairs["pair_index"] == 4, "delta_hpa"].iloc[0])
    assert pairs.loc[pairs["pair_index"] == 0, "delta_hpa"].iloc[0] == pytest.approx(
        0.2
    )
    carried = convergence.query(
        "pair_index == 0 and mode == 'guided' and generation == 2"
    ).iloc[0]
    assert bool(carried["terminal_carried_forward"])
    assert "best_resolved_offering_count" in convergence
    assert (
        convergence.loc[
            convergence["mode"] == "unguided", "best_fitness_guidance_attainment"
        ]
        == 0
    ).all()
    assert manifest["run_counts"]["guided"] == {
        "total": 10,
        "succeeded": 9,
        "failed": 1,
    }
    assert set(manifest["outputs_sha256"]) == expected - {"analysis_manifest.json"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows[0].__setitem__(
                "config",
                json.dumps(evaluation_config(population_size=99)),
            ),
            "one input_fingerprint and one GA config",
        ),
        (
            lambda rows: rows[0].__setitem__("random_seed", 101),
            "must share one seed",
        ),
        (
            lambda rows: rows[0].__setitem__("generation_metrics", ""),
            "non-empty JSON array",
        ),
        (
            lambda rows: rows[0].__setitem__(
                "historical_score_breakdown",
                json.dumps(historical_score_breakdown(0.2)),
            ),
            "aggregate does not match the run HPA",
        ),
        (
            lambda rows: rows[0].__setitem__("termination_reason", "UNKNOWN"),
            "invalid termination_reason",
        ),
        (
            lambda rows: rows[1].__setitem__("termination_reason", "OPTIMAL_REACHED"),
            "OPTIMAL_REACHED is legacy evidence",
        ),
        (
            lambda rows: rows[1].__setitem__("termination_reason", "STAGNATION"),
            "termination_reason must be MAX_GENERATIONS",
        ),
        (
            plateau_then_improve,
            "continue past the STAGNATION stop at generation 1",
        ),
        (
            lambda rows: (
                plateau_at_max_generations(rows),
                rows[2].__setitem__("termination_reason", "STAGNATION"),
            ),
            "termination_reason must be MAX_GENERATIONS",
        ),
        (
            lambda rows: rows[8].__setitem__("elapsed_seconds", 1.0),
            "failed but has outcome fields",
        ),
        (
            add_unguided_guidance,
            "unguided fitness must not use historical guidance",
        ),
        (
            lambda rows: rows[0].__setitem__(
                "config", json.dumps(evaluation_config(population_size=0))
            ),
            "population_size and max_generations must be positive",
        ),
    ],
)
def test_analysis_rejects_inconsistent_matched_evidence(tmp_path: Path, mutate, message):
    rows = evaluation_rows()
    mutate(rows)
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)

    with pytest.raises(ValueError, match=message):
        analysis.analyze(source, generation_manifest, tmp_path / "analysis")

    assert not (tmp_path / "analysis").exists()


def test_analysis_prefers_max_generations_when_stagnation_coincides(tmp_path: Path):
    rows = evaluation_rows()
    plateau_at_max_generations(rows)
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)

    analysis.analyze(source, generation_manifest, tmp_path / "analysis")


def test_analysis_never_overwrites_a_prior_run(tmp_path: Path):
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    output = tmp_path / "analysis"
    rows = evaluation_rows()
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)
    output.mkdir()
    marker = output / "reviewed.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        analysis.analyze(source, generation_manifest, output)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_analysis_rejects_stale_generation_manifest(tmp_path: Path):
    rows = evaluation_rows()
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)
    with source.open("a", encoding="utf-8") as destination:
        destination.write("\n")

    with pytest.raises(ValueError, match="CSV hash does not match"):
        analysis.analyze(source, generation_manifest, tmp_path / "analysis")

    assert not (tmp_path / "analysis").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest.__setitem__("schema_version", True),
            "identity is invalid",
        ),
        (
            lambda manifest: manifest.__setitem__(
                "generated_at_utc", "2026-10-03T08:00:00+08:00"
            ),
            "must use UTC",
        ),
        (
            lambda manifest: manifest["execution"].__setitem__(
                "concurrent_runs", True
            ),
            "requires sequential runs",
        ),
        (
            lambda manifest: manifest["execution"]["run_order"].reverse(),
            "run_order does not match",
        ),
    ],
)
def test_analysis_rejects_invalid_generation_provenance(
    tmp_path: Path, mutate, message
):
    rows = evaluation_rows()
    source = tmp_path / "evaluation_batch.csv"
    generation_manifest = tmp_path / "evaluation_manifest.json"
    write_export(source, rows)
    write_generation_manifest(generation_manifest, source, rows)
    content = json.loads(generation_manifest.read_text(encoding="utf-8"))
    mutate(content)
    generation_manifest.write_text(json.dumps(content), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        analysis.analyze(source, generation_manifest, tmp_path / "analysis")

    assert not (tmp_path / "analysis").exists()
