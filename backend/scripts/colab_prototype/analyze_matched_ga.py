"""CHR-101 / CHR-COLAB-005 matched-seed GA analysis."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import UUID

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt
import pandas as pd


TRACEABILITY = "CHR-101 / CHR-COLAB-005"
MODES = ("guided", "unguided")
PAIR_COUNT = 10
SEED_MIN = -(2**63)
SEED_MAX = 2**63 - 1
TERMINATION_REASONS = ("MAX_GENERATIONS", "OPTIMAL_REACHED", "STAGNATION")
HISTORICAL_SCORE_FIELDS = (
    "teacher_subject_score",
    "teacher_day_score",
    "teacher_time_score",
    "teacher_subject_room_score",
    "historical_pattern_attainment",
)
GA_CONFIG_FIELDS = (
    "population_size",
    "max_generations",
    "crossover_rate",
    "mutation_rate",
    "gene_mutation_rate",
    "tournament_size",
    "elite_count",
    "stagnation_limit",
    "random_seed",
    "meeting_day_patterns",
)
GENERATION_MANIFEST_FIELDS = (
    "schema_version",
    "traceability",
    "evaluation_csv_sha256",
    "input_fingerprint",
    "generator_commit",
    "generated_at_utc",
    "environment",
    "execution",
    "scoring",
)
HISTORICAL_WEIGHT_FIELDS = (
    "teacher_subject",
    "teacher_day",
    "teacher_time",
    "teacher_subject_room",
)
REQUIRED_COLUMNS = (
    "pair_index",
    "mode",
    "random_seed",
    "status",
    "termination_reason",
    "termination_generation",
    "elapsed_seconds",
    "required_section_count",
    "assigned_section_count",
    "assignment_completeness",
    "hard_conflict_count",
    "historical_pattern_attainment",
    "historical_score_breakdown",
    "generation_metrics",
    "error_message",
    "input_fingerprint",
    "config",
)
SUMMARY_METRICS = (
    "assignment_completeness",
    "hard_conflict_count",
    "historical_pattern_attainment",
    "elapsed_seconds",
    "termination_generation",
)


@dataclass(frozen=True, slots=True)
class ValidatedExport:
    runs: pd.DataFrame
    histories: dict[tuple[int, str], list[dict[str, Any]]]
    input_fingerprint: str
    config: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _integer(value: object, field: str) -> int:
    text = _text(value)
    try:
        parsed = int(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an integer") from error
    if str(parsed) != text and not (text.startswith("+") and str(parsed) == text[1:]):
        raise ValueError(f"{field} must be an integer")
    return parsed


def _number(value: object, field: str) -> float:
    try:
        parsed = float(_text(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _strict_json(value: object, field: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"{field} contains non-finite JSON value {constant}")

    try:
        return json.loads(_text(value), parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{field} must contain valid JSON") from error


def _validate_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    if set(config) != set(GA_CONFIG_FIELDS):
        raise ValueError("config must contain the exact exported GA fields")

    population_size = _integer(config["population_size"], "config.population_size")
    max_generations = _integer(config["max_generations"], "config.max_generations")
    tournament_size = _integer(config["tournament_size"], "config.tournament_size")
    elite_count = _integer(config["elite_count"], "config.elite_count")
    random_seed = _integer(config["random_seed"], "config.random_seed")
    if population_size <= 0 or max_generations <= 0:
        raise ValueError("config population_size and max_generations must be positive")
    if not 2 <= tournament_size <= population_size:
        raise ValueError("config tournament_size is outside its valid range")
    if not 0 <= elite_count < population_size:
        raise ValueError("config elite_count is outside its valid range")
    if not SEED_MIN <= random_seed <= SEED_MAX:
        raise ValueError("config.random_seed must fit a signed 64-bit integer")

    for field in ("crossover_rate", "mutation_rate", "gene_mutation_rate"):
        if not 0 <= _number(config[field], f"config.{field}") <= 1:
            raise ValueError(f"config.{field} must be between 0 and 1")
    stagnation_limit = config["stagnation_limit"]
    if stagnation_limit is not None and _integer(
        stagnation_limit, "config.stagnation_limit"
    ) <= 0:
        raise ValueError("config.stagnation_limit must be positive or null")
    patterns = config["meeting_day_patterns"]
    if (
        not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
    ):
        raise ValueError("config.meeting_day_patterns must be a non-empty string array")
    return config


def _validate_score_breakdown(
    raw: object, *, pair_index: int, mode: str, expected_attainment: float
) -> dict[str, float | None]:
    field = f"pair {pair_index} {mode} historical_score_breakdown"
    breakdown = _strict_json(raw, field)
    if not isinstance(breakdown, dict) or set(breakdown) != set(
        HISTORICAL_SCORE_FIELDS
    ):
        raise ValueError(f"{field} must contain the four streams and aggregate HPA")
    normalized: dict[str, float | None] = {}
    for score_field in HISTORICAL_SCORE_FIELDS:
        value = breakdown[score_field]
        if value is None and score_field != "historical_pattern_attainment":
            normalized[score_field] = None
            continue
        score = _number(value, f"{field}.{score_field}")
        if not 0 <= score <= 1:
            raise ValueError(f"{field}.{score_field} must be between 0 and 1")
        normalized[score_field] = score
    if not math.isclose(
        normalized["historical_pattern_attainment"],
        expected_attainment,
        abs_tol=1e-12,
    ):
        raise ValueError(f"{field} aggregate does not match the run HPA")
    return normalized


def _validate_history(
    raw: object,
    *,
    pair_index: int,
    mode: str,
    termination_generation: int,
    hard_conflict_count: int,
    outcome_historical_pattern_attainment: float,
) -> list[dict[str, Any]]:
    field = f"pair {pair_index} {mode} generation_metrics"
    if not _text(raw):
        raise ValueError(f"{field} must be a non-empty JSON array")
    history = _strict_json(raw, field)
    if not isinstance(history, list) or not history:
        raise ValueError(f"{field} must be a non-empty JSON array")

    normalized = []
    for expected_generation, item in enumerate(history):
        if not isinstance(item, dict):
            raise ValueError(f"{field} entries must be objects")
        generation = _integer(item.get("generation"), f"{field}.generation")
        if generation != expected_generation:
            raise ValueError(f"{field} generations must be contiguous from zero")
        fitness = item.get("best_fitness")
        if not isinstance(fitness, list) or len(fitness) != 3:
            raise ValueError(f"{field}.best_fitness must contain three values")
        assigned = _number(fitness[0], f"{field}.best_fitness[0]")
        negative_conflicts = _number(fitness[1], f"{field}.best_fitness[1]")
        attainment = _number(fitness[2], f"{field}.best_fitness[2]")
        mean_completeness = _number(
            item.get("mean_assignment_completeness"),
            f"{field}.mean_assignment_completeness",
        )
        if assigned < 0 or not assigned.is_integer():
            raise ValueError(f"{field} assigned-count fitness must be a non-negative integer")
        if negative_conflicts > 0 or not negative_conflicts.is_integer():
            raise ValueError(f"{field} conflict fitness must be a non-positive integer")
        if not 0 <= attainment <= 1 or not 0 <= mean_completeness <= 1:
            raise ValueError(f"{field} completeness and attainment must be between 0 and 1")
        if mode == "unguided" and not math.isclose(attainment, 0.0, abs_tol=1e-12):
            raise ValueError(f"{field} unguided fitness must not use historical guidance")
        normalized.append(
            {
                "generation": generation,
                "best_fitness": [assigned, negative_conflicts, attainment],
                "mean_assignment_completeness": mean_completeness,
            }
        )

    final = normalized[-1]
    if final["generation"] != termination_generation:
        raise ValueError(f"{field} must end at termination_generation")
    if not math.isclose(-final["best_fitness"][1], hard_conflict_count):
        raise ValueError(f"{field} final hard-conflict count does not match the run")
    if mode == "guided" and not math.isclose(
        final["best_fitness"][2],
        outcome_historical_pattern_attainment,
        abs_tol=1e-12,
    ):
        raise ValueError(f"{field} final guided attainment does not match the outcome")
    return normalized


def _expected_termination(
    history: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> tuple[int, str | None]:
    """Replay the engine's stop rule; return the first stop (or the last entry)."""
    stagnant_generations = 0
    for index, current in enumerate(history):
        if index:
            stagnant_generations = (
                0
                if tuple(current["best_fitness"]) > tuple(history[index - 1]["best_fitness"])
                else stagnant_generations + 1
            )
        generation = current["generation"]
        # CHR-GA-006: a feasible run keeps evolving, so only the generation and
        # stagnation limits can end it; MAX_GENERATIONS wins when both apply.
        if generation >= config["max_generations"]:
            return generation, "MAX_GENERATIONS"
        stagnation_limit = config["stagnation_limit"]
        if stagnation_limit is not None and stagnant_generations >= stagnation_limit:
            return generation, "STAGNATION"
    return history[-1]["generation"], None


def load_evaluation_export(path: Path) -> ValidatedExport:
    """Validate the complete CHR-38 CSV contract without dropping failed runs."""
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".csv" or not path.is_file():
        raise ValueError("evaluation input must be a CSV file")
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"evaluation export is missing columns: {', '.join(missing)}")
        source_rows = list(reader)
    if len(source_rows) != PAIR_COUNT * len(MODES):
        raise ValueError("evaluation export must retain exactly 20 guided/unguided runs")

    rows: list[dict[str, Any]] = []
    histories: dict[tuple[int, str], list[dict[str, Any]]] = {}
    fingerprints: set[str] = set()
    configs: dict[str, dict[str, Any]] = {}
    for source_row in source_rows:
        pair_index = _integer(source_row["pair_index"], "pair_index")
        mode = _text(source_row["mode"])
        status = _text(source_row["status"])
        random_seed = _integer(source_row["random_seed"], "random_seed")
        if mode not in MODES:
            raise ValueError("mode must be guided or unguided")
        if status not in ("succeeded", "failed"):
            raise ValueError("status must be succeeded or failed")
        if not SEED_MIN <= random_seed <= SEED_MAX:
            raise ValueError("random_seed must fit a signed 64-bit integer")

        fingerprint = _text(source_row["input_fingerprint"]).lower()
        if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
            raise ValueError("input_fingerprint must be a SHA-256 hex digest")
        fingerprints.add(fingerprint)
        config = _validate_config(_strict_json(source_row["config"], "config"))
        max_generations = config["max_generations"]
        config_key = json.dumps(config, sort_keys=True, separators=(",", ":"))
        configs[config_key] = config

        row: dict[str, Any] = {
            "pair_index": pair_index,
            "mode": mode,
            "random_seed": random_seed,
            "status": status,
            "termination_reason": _text(source_row["termination_reason"]),
            "error_message": _text(source_row["error_message"]),
            "input_fingerprint": fingerprint,
            "config": config_key,
        }
        if status == "failed":
            if not row["error_message"]:
                raise ValueError(f"pair {pair_index} {mode} failed without an error_message")
            outcome_fields = (
                "termination_reason",
                "termination_generation",
                "elapsed_seconds",
                "required_section_count",
                "assigned_section_count",
                "assignment_completeness",
                "hard_conflict_count",
                "historical_pattern_attainment",
                "historical_score_breakdown",
                "generation_metrics",
            )
            populated = [field for field in outcome_fields if _text(source_row[field])]
            if populated:
                raise ValueError(
                    f"pair {pair_index} {mode} failed but has outcome fields: "
                    + ", ".join(populated)
                )
            for field in SUMMARY_METRICS:
                row[field] = None
            for field in ("required_section_count", "assigned_section_count"):
                row[field] = None
            row["historical_score_breakdown"] = None
            row["generation_metrics"] = ""
        else:
            if row["error_message"]:
                raise ValueError(f"pair {pair_index} {mode} succeeded but has an error_message")
            termination_reason = row["termination_reason"]
            if termination_reason not in TERMINATION_REASONS:
                raise ValueError(
                    f"pair {pair_index} {mode} has an invalid termination_reason"
                )
            termination_generation = _integer(
                source_row["termination_generation"], "termination_generation"
            )
            elapsed_seconds = _number(source_row["elapsed_seconds"], "elapsed_seconds")
            required_count = _integer(
                source_row["required_section_count"], "required_section_count"
            )
            assigned_count = _integer(
                source_row["assigned_section_count"], "assigned_section_count"
            )
            completeness = _number(
                source_row["assignment_completeness"], "assignment_completeness"
            )
            conflicts = _integer(source_row["hard_conflict_count"], "hard_conflict_count")
            attainment = _number(
                source_row["historical_pattern_attainment"],
                "historical_pattern_attainment",
            )
            if termination_generation < 0 or elapsed_seconds < 0:
                raise ValueError("termination generation and elapsed time must be non-negative")
            if termination_generation > max_generations:
                raise ValueError("termination_generation exceeds config.max_generations")
            if required_count <= 0 or not 0 <= assigned_count <= required_count:
                raise ValueError("section counts must satisfy 0 <= assigned <= required")
            if conflicts < 0 or not 0 <= attainment <= 1:
                raise ValueError("conflicts must be non-negative and attainment must be between 0 and 1")
            score_breakdown = _validate_score_breakdown(
                source_row["historical_score_breakdown"],
                pair_index=pair_index,
                mode=mode,
                expected_attainment=attainment,
            )
            expected_completeness = assigned_count / required_count
            if not math.isclose(completeness, expected_completeness, abs_tol=1e-12):
                raise ValueError("assignment_completeness does not match section counts")
            completeness = expected_completeness
            history = _validate_history(
                source_row["generation_metrics"],
                pair_index=pair_index,
                mode=mode,
                termination_generation=termination_generation,
                hard_conflict_count=conflicts,
                outcome_historical_pattern_attainment=attainment,
            )
            if termination_reason == "OPTIMAL_REACHED":
                raise ValueError(
                    f"pair {pair_index} {mode} termination_reason OPTIMAL_REACHED "
                    "is legacy evidence from before CHR-GA-006 stopped feasible "
                    "runs early; regenerate the evaluation"
                )
            stop_generation, expected_reason = _expected_termination(
                history,
                config=config,
            )
            if stop_generation != termination_generation:
                raise ValueError(
                    f"pair {pair_index} {mode} generation_metrics continue past "
                    f"the {expected_reason} stop at generation {stop_generation}"
                )
            if termination_reason != expected_reason:
                raise ValueError(
                    f"pair {pair_index} {mode} termination_reason must be "
                    f"{expected_reason or 'a later generation'}"
                )
            histories[(pair_index, mode)] = history
            row.update(
                {
                    "termination_generation": termination_generation,
                    "elapsed_seconds": elapsed_seconds,
                    "required_section_count": required_count,
                    "assigned_section_count": assigned_count,
                    "assignment_completeness": completeness,
                    "hard_conflict_count": conflicts,
                    "historical_pattern_attainment": attainment,
                    "historical_score_breakdown": json.dumps(
                        score_breakdown, sort_keys=True, separators=(",", ":")
                    ),
                    "generation_metrics": json.dumps(
                        history, sort_keys=True, separators=(",", ":")
                    ),
                }
            )
        row["is_feasible"] = bool(
            status == "succeeded"
            and row["assignment_completeness"] == 1
            and row["hard_conflict_count"] == 0
        )
        rows.append(row)

    if len(fingerprints) != 1 or len(configs) != 1:
        raise ValueError("all runs must share one input_fingerprint and one GA config")
    frame = pd.DataFrame(rows).sort_values(["pair_index", "mode"]).reset_index(drop=True)
    if set(frame["pair_index"]) != set(range(PAIR_COUNT)):
        raise ValueError("pair_index values must cover 0 through 9")
    if frame.duplicated(["pair_index", "mode"]).any():
        raise ValueError("each pair must contain one guided and one unguided run")
    if frame.groupby("pair_index")["mode"].nunique().ne(len(MODES)).any():
        raise ValueError("each pair must contain both guided and unguided modes")
    if frame.groupby("pair_index")["random_seed"].nunique().ne(1).any():
        raise ValueError("guided and unguided runs in a pair must share one seed")
    if frame.groupby("pair_index")["random_seed"].first().nunique() != PAIR_COUNT:
        raise ValueError("the ten matched seeds must be unique")
    return ValidatedExport(frame, histories, fingerprints.pop(), next(iter(configs.values())))


def load_generation_manifest(
    path: Path, input_path: Path, validated: ValidatedExport
) -> dict[str, Any]:
    """Validate reviewed backend-generation provenance for timing and HPA evidence."""
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("generation manifest must be a JSON file")
    manifest = _strict_json(path.read_text(encoding="utf-8"), "generation manifest")
    if not isinstance(manifest, dict) or set(manifest) != set(
        GENERATION_MANIFEST_FIELDS
    ):
        raise ValueError("generation manifest has an unexpected schema")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["traceability"] != "CHR-38 / CHR-EVAL-001"
    ):
        raise ValueError("generation manifest identity is invalid")
    if manifest["evaluation_csv_sha256"] != _sha256(input_path):
        raise ValueError("generation manifest CSV hash does not match the input")
    if manifest["input_fingerprint"] != validated.input_fingerprint:
        raise ValueError("generation manifest input fingerprint does not match the export")
    generator_commit = manifest["generator_commit"]
    if (
        not isinstance(generator_commit, str)
        or len(generator_commit) != 40
        or any(character not in "0123456789abcdef" for character in generator_commit)
    ):
        raise ValueError("generation manifest requires a lowercase full generator commit")
    try:
        generated_at = datetime.fromisoformat(manifest["generated_at_utc"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("generation manifest requires an ISO-8601 generated_at_utc") from error
    if generated_at.tzinfo is None or generated_at.utcoffset() != timedelta(0):
        raise ValueError("generation manifest generated_at_utc must use UTC")

    environment = manifest["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "python",
        "platform",
        "processor",
    } or any(not isinstance(value, str) or not value.strip() for value in environment.values()):
        raise ValueError("generation manifest environment is incomplete")

    execution = manifest["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "concurrent_runs",
        "shared_runtime",
        "run_order",
    }:
        raise ValueError("generation manifest execution controls are incomplete")
    if (
        type(execution["concurrent_runs"]) is not int
        or execution["concurrent_runs"] != 1
        or execution["shared_runtime"] is not True
    ):
        raise ValueError("matched timing requires sequential runs in one shared runtime")
    expected_order = [
        {
            "pair_index": int(run.pair_index),
            "mode": run.mode,
            "random_seed": int(run.random_seed),
        }
        for run in validated.runs.itertuples(index=False)
    ]
    if execution["run_order"] != expected_order:
        raise ValueError("generation manifest run_order does not match the export")

    scoring = manifest["scoring"]
    if not isinstance(scoring, dict) or set(scoring) != {
        "implementation",
        "rule_set_id",
        "rule_set_sha256",
        "weights",
    }:
        raise ValueError("generation manifest scoring provenance is incomplete")
    if not isinstance(scoring["implementation"], str) or not scoring["implementation"].strip():
        raise ValueError("generation manifest scorer implementation is required")
    try:
        UUID(str(scoring["rule_set_id"]))
    except ValueError as error:
        raise ValueError("generation manifest rule_set_id must be a UUID") from error
    rule_set_sha256 = scoring["rule_set_sha256"]
    if not isinstance(rule_set_sha256, str) or len(rule_set_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in rule_set_sha256
    ):
        raise ValueError("generation manifest rule_set_sha256 must be a SHA-256 digest")
    weights = scoring["weights"]
    if not isinstance(weights, dict) or set(weights) != set(HISTORICAL_WEIGHT_FIELDS):
        raise ValueError("generation manifest must contain all four historical weights")
    normalized_weights = [
        _number(weights[field], f"generation manifest scoring.weights.{field}")
        for field in HISTORICAL_WEIGHT_FIELDS
    ]
    if any(not 0 <= weight <= 1 for weight in normalized_weights) or not math.isclose(
        sum(normalized_weights), 1.0, abs_tol=1e-12
    ):
        raise ValueError("generation manifest historical weights must sum to one")
    return manifest


def build_mode_summary(runs: pd.DataFrame) -> pd.DataFrame:
    records = []
    for mode in MODES:
        selected = runs[runs["mode"] == mode]
        succeeded = selected[selected["status"] == "succeeded"]
        attainment = succeeded["historical_pattern_attainment"].dropna().astype(float)
        records.append(
            {
                "mode": mode,
                "total_runs": len(selected),
                "succeeded_runs": len(succeeded),
                "failed_runs": int((selected["status"] == "failed").sum()),
                "feasible_runs": int(selected["is_feasible"].sum()),
                "feasible_run_rate": float(selected["is_feasible"].mean()),
                "scheduling_consistency_hpa": (
                    float(attainment.std(ddof=1)) if len(attainment) > 1 else math.nan
                ),
            }
        )
    return pd.DataFrame(records)


def build_descriptive_statistics(runs: pd.DataFrame) -> pd.DataFrame:
    records = []
    for mode in MODES:
        succeeded = runs[(runs["mode"] == mode) & (runs["status"] == "succeeded")]
        for metric in SUMMARY_METRICS:
            values = succeeded[metric].dropna().astype(float)
            records.append(
                {
                    "mode": mode,
                    "metric": metric,
                    "count": len(values),
                    "mean": values.mean() if len(values) else math.nan,
                    "median": values.median() if len(values) else math.nan,
                    "minimum": values.min() if len(values) else math.nan,
                    "maximum": values.max() if len(values) else math.nan,
                    "standard_deviation": (
                        values.std(ddof=1) if len(values) > 1 else math.nan
                    ),
                }
            )
    return pd.DataFrame(records)


def build_matched_differences(runs: pd.DataFrame) -> pd.DataFrame:
    records = []
    for pair_index in range(PAIR_COUNT):
        pair = runs[runs["pair_index"] == pair_index].set_index("mode")
        guided = pair.loc["guided"]
        unguided = pair.loc["unguided"]
        comparable = guided["status"] == unguided["status"] == "succeeded"
        records.append(
            {
                "pair_index": pair_index,
                "random_seed": int(guided["random_seed"]),
                "guided_status": guided["status"],
                "unguided_status": unguided["status"],
                "guided_hpa": guided["historical_pattern_attainment"],
                "unguided_hpa": unguided["historical_pattern_attainment"],
                "delta_hpa": (
                    float(guided["historical_pattern_attainment"])
                    - float(unguided["historical_pattern_attainment"])
                    if comparable
                    else math.nan
                ),
                "comparable": comparable,
            }
        )
    return pd.DataFrame(records)


def build_convergence(
    runs: pd.DataFrame,
    histories: dict[tuple[int, str], list[dict[str, Any]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    max_generation = max(
        (history[-1]["generation"] for history in histories.values()), default=0
    )
    records = []
    for run in runs.itertuples(index=False):
        history = histories.get((run.pair_index, run.mode))
        if history is None:
            continue
        terminal = history[-1]
        for generation in range(max_generation + 1):
            item = history[generation] if generation < len(history) else terminal
            records.append(
                {
                    "pair_index": run.pair_index,
                    "random_seed": run.random_seed,
                    "mode": run.mode,
                    "generation": generation,
                    "terminal_carried_forward": generation >= len(history),
                    "best_resolved_offering_count": item["best_fitness"][0],
                    "best_hard_conflict_count": -item["best_fitness"][1],
                    "best_fitness_guidance_attainment": item["best_fitness"][2],
                    "mean_population_offering_completeness": item[
                        "mean_assignment_completeness"
                    ],
                }
            )
    columns = (
        "pair_index",
        "random_seed",
        "mode",
        "generation",
        "terminal_carried_forward",
        "best_resolved_offering_count",
        "best_hard_conflict_count",
        "best_fitness_guidance_attainment",
        "mean_population_offering_completeness",
    )
    by_run = pd.DataFrame(records, columns=columns)
    if by_run.empty:
        return by_run, pd.DataFrame(
            columns=(
                "mode",
                "generation",
                "run_count",
                "mean_best_resolved_offering_count",
                "mean_best_hard_conflict_count",
                "mean_best_fitness_guidance_attainment",
                "mean_population_offering_completeness",
            )
        )
    summary = (
        by_run.groupby(["mode", "generation"], sort=True)
        .agg(
            run_count=("pair_index", "count"),
            mean_best_resolved_offering_count=(
                "best_resolved_offering_count", "mean"
            ),
            mean_best_hard_conflict_count=("best_hard_conflict_count", "mean"),
            mean_best_fitness_guidance_attainment=(
                "best_fitness_guidance_attainment",
                "mean",
            ),
            mean_population_offering_completeness=(
                "mean_population_offering_completeness",
                "mean",
            ),
        )
        .reset_index()
    )
    return by_run, summary


def _save_figure(figure: plt.Figure, path: Path, title: str) -> None:
    figure.savefig(
        path,
        format="svg",
        bbox_inches="tight",
        metadata={"Title": title, "Creator": TRACEABILITY, "Date": None},
    )
    plt.close(figure)


def _plot_boxplots(runs: pd.DataFrame, path: Path) -> None:
    metrics = (
        ("assignment_completeness", "Assignment completeness", (0, 1.05)),
        ("hard_conflict_count", "Hard-conflict count", None),
        ("historical_pattern_attainment", "Historical-pattern attainment", (0, 1.05)),
        ("elapsed_seconds", "Time to generate (seconds)", None),
    )
    figure, axes = plt.subplots(2, 2, figsize=(11, 8))
    succeeded = runs[runs["status"] == "succeeded"]
    for axis, (metric, title, limits) in zip(axes.flat, metrics, strict=True):
        values = [
            succeeded.loc[succeeded["mode"] == mode, metric].dropna().astype(float).tolist()
            for mode in MODES
        ]
        present = [(mode, value) for mode, value in zip(MODES, values, strict=True) if value]
        if present:
            boxes = axis.boxplot(
                [value for _, value in present],
                tick_labels=[mode.title() for mode, _ in present],
                patch_artist=True,
            )
            for patch, color in zip(boxes["boxes"], ("#16697a", "#db6400"), strict=False):
                patch.set_facecolor(color)
                patch.set_alpha(0.65)
        else:
            axis.text(0.5, 0.5, "No successful runs", ha="center", va="center")
            axis.set_xticks([])
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.25)
        if limits:
            axis.set_ylim(*limits)
    figure.suptitle("Matched guided and unguided GA metrics")
    figure.tight_layout()
    _save_figure(figure, path, "Matched guided and unguided GA metrics")


def _plot_matched_seeds(pairs: pd.DataFrame, path: Path) -> None:
    x = list(range(PAIR_COUNT))
    labels = [str(seed) for seed in pairs["random_seed"]]
    figure, (attainment_axis, delta_axis) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for mode, color in (("guided", "#16697a"), ("unguided", "#db6400")):
        values = pairs[f"{mode}_hpa"].astype(float)
        attainment_axis.plot(x, values, marker="o", label=mode.title(), color=color)
        failed = pairs[f"{mode}_status"] == "failed"
        attainment_axis.scatter(
            pairs.index[failed],
            [-0.06] * int(failed.sum()),
            marker="x",
            color=color,
            label=f"{mode.title()} failed" if failed.any() else None,
        )
    attainment_axis.set_ylabel("Historical-pattern attainment")
    attainment_axis.set_ylim(-0.1, 1.05)
    attainment_axis.grid(alpha=0.25)
    attainment_axis.legend(ncols=2)

    comparable = pairs["comparable"]
    colors = ["#16697a" if value >= 0 else "#db6400" for value in pairs.loc[comparable, "delta_hpa"]]
    delta_axis.bar(pairs.index[comparable], pairs.loc[comparable, "delta_hpa"], color=colors)
    for index in pairs.index[~comparable]:
        delta_axis.text(index, 0, "not comparable", rotation=90, ha="center", va="center", fontsize=8)
    delta_axis.axhline(0, color="black", linewidth=0.8)
    delta_axis.set_ylabel("Guided - unguided HPA")
    delta_axis.set_xlabel("Matched random seed")
    delta_axis.set_xticks(x, labels, rotation=45, ha="right")
    delta_axis.grid(axis="y", alpha=0.25)
    figure.suptitle("Historical-pattern attainment by matched seed")
    figure.tight_layout()
    _save_figure(figure, path, "Historical-pattern attainment by matched seed")


def _plot_convergence(summary: pd.DataFrame, path: Path) -> None:
    metrics = (
        ("mean_best_resolved_offering_count", "Best resolved-offering count"),
        ("mean_best_hard_conflict_count", "Best hard-conflict count"),
        (
            "mean_best_fitness_guidance_attainment",
            "Fitness guidance attainment\n(disabled for unguided)",
        ),
    )
    figure, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    if summary.empty:
        for axis in axes:
            axis.text(0.5, 0.5, "No successful generation histories", ha="center", va="center")
            axis.set_xticks([])
    else:
        for axis, (metric, label) in zip(axes, metrics, strict=True):
            for mode, color in (("guided", "#16697a"), ("unguided", "#db6400")):
                selected = summary[summary["mode"] == mode]
                if not selected.empty:
                    axis.plot(
                        selected["generation"],
                        selected[metric],
                        label=mode.title(),
                        color=color,
                    )
            axis.set_ylabel(label)
            axis.grid(alpha=0.25)
            axis.legend()
        axes[-1].set_xlabel("Generation")
    figure.suptitle(
        "Lexicographic fitness convergence\nTerminal best values are carried forward after early stopping"
    )
    figure.tight_layout()
    _save_figure(figure, path, "Lexicographic fitness convergence")


def analyze(
    input_path: Path, generation_manifest_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Create auditable tables and vector figures without claiming official results."""
    input_path = input_path.resolve(strict=True)
    output_dir = output_dir.resolve(strict=False)
    if output_dir.exists():
        raise ValueError("output directory already exists; use a new run directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    validated = load_evaluation_export(input_path)
    generation_manifest = load_generation_manifest(
        generation_manifest_path, input_path, validated
    )
    mode_summary = build_mode_summary(validated.runs)
    descriptive = build_descriptive_statistics(validated.runs)
    pairs = build_matched_differences(validated.runs)
    convergence_by_run, convergence_summary = build_convergence(
        validated.runs, validated.histories
    )

    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        tables = {
            "validated_runs.csv": validated.runs,
            "mode_summary.csv": mode_summary,
            "descriptive_statistics.csv": descriptive,
            "matched_seed_differences.csv": pairs,
            "convergence_by_run.csv": convergence_by_run,
            "convergence_summary.csv": convergence_summary,
        }
        for name, frame in tables.items():
            frame.to_csv(staging / name, index=False, lineterminator="\n", na_rep="")
        _plot_boxplots(validated.runs, staging / "metric_boxplots.svg")
        _plot_matched_seeds(pairs, staging / "matched_seed_hpa.svg")
        _plot_convergence(convergence_summary, staging / "fitness_convergence.svg")

        generated = sorted(path for path in staging.iterdir() if path.is_file())
        manifest = {
            "schema_version": 1,
            "traceability": TRACEABILITY,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "filename": input_path.name,
                "sha256": _sha256(input_path),
                "generation_manifest_filename": generation_manifest_path.name,
                "generation_manifest_sha256": _sha256(generation_manifest_path),
                "generator_commit": generation_manifest["generator_commit"],
                "rule_set_id": generation_manifest["scoring"]["rule_set_id"],
                "rule_set_sha256": generation_manifest["scoring"]["rule_set_sha256"],
                "input_fingerprint": validated.input_fingerprint,
                "config": validated.config,
            },
            "run_counts": {
                mode: {
                    "total": int((validated.runs["mode"] == mode).sum()),
                    "succeeded": int(
                        (
                            (validated.runs["mode"] == mode)
                            & (validated.runs["status"] == "succeeded")
                        ).sum()
                    ),
                    "failed": int(
                        (
                            (validated.runs["mode"] == mode)
                            & (validated.runs["status"] == "failed")
                        ).sum()
                    ),
                }
                for mode in MODES
            },
            "notes": [
                "Failed runs remain in the ledgers and feasible-run denominator.",
                "Descriptive metrics exclude unavailable values from failed runs.",
                "Delta HPA is blank unless both matched runs succeeded.",
                "The generation manifest binds timing and scorer provenance to the CSV.",
                "Outcome HPA is independently scored for both modes; unguided fitness guidance remains zero.",
                "Convergence reports offering-level fitness dimensions and carries terminal best values forward.",
                "This artifact does not by itself establish an official research result.",
            ],
            "outputs_sha256": {path.name: _sha256(path) for path in generated},
        }
        (staging / "analysis_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staging, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and analyze a CHR-38 matched GA evaluation CSV."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--generation-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    manifest = analyze(
        arguments.input, arguments.generation_manifest, arguments.output_dir
    )
    print(
        json.dumps(
            {
                "output_dir": str(arguments.output_dir),
                "source_sha256": manifest["source"]["sha256"],
                "run_counts": manifest["run_counts"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
