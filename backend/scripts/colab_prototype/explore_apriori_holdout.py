"""CHR-99 / CHR-COLAB-003 training-only Apriori exploration and holdout evaluation.

``explore`` reads only the training artifact and produces threshold
comparisons. ``freeze`` locks an explored selection. ``verify`` checks the lock
and training/source provenance without opening holdout data. ``evaluate``
repeats those checks before reading the chronological holdout artifact.
No database is required.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import product
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from app.services.arm.mining import (  # noqa: E402
    MinedRule,
    MiningThresholds,
    RuleMiner,
    StreamMiningResult,
)
from app.services.arm.preprocessing import (  # noqa: E402
    HistoricalOffering,
    HistoricalInclusionPolicy,
    HistoricalPartition,
    HistoricalTerm,
    PatternStream,
    StreamTrainingData,
    TransactionBuilder,
)


TRACEABILITY = "CHR-99 / CHR-COLAB-003"
HOLDOUT_TERM_COUNT = 3
_METRIC_SCALE = Decimal("0.0001")
_ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{4})$")
_CLOCK_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Same pure normalization as the preprocessor, which Colab does not download.
_DAY_TOKENS = (("TH", "Th"), ("SAT", "Sa"), ("SA", "Sa"), ("SUN", "Su"), ("SU", "Su"), ("M", "M"), ("T", "T"), ("W", "W"), ("F", "F"))
_DAY_ORDER = ("M", "T", "W", "Th", "F", "Sa", "Su")
_GRID_FIELDS = (
    "min_support",
    "min_confidence",
    "min_lift",
    "min_term_recurrence",
)
_REQUIRED_RECORD_FIELDS = frozenset(
    {
        "historical_record_id",
        "academic_year",
        "term_number",
        "canonical_course_code",
        "faculty_name",
        "meeting_days",
        "start_time",
        "end_time",
        "room_code",
        "teacher_subject_eligible",
        "full_stream_eligible",
    }
)
_EXPLORATION_FIELDS = (
    "stream",
    *_GRID_FIELDS,
    "eligible_training_term_count",
    "mined_training_term_count",
    "transaction_count",
    "retained_rule_count",
    "mean_support",
    "mean_confidence",
    "mean_lift",
    "mean_term_recurrence",
    "mean_guidance_score",
    "skipped_reason",
)
_RULE_FIELDS = (
    "stream",
    "research_faculty_key",
    "faculty_name",
    "subject_code",
    "room_code",
    "meeting_days",
    "time_slot",
    "support",
    "confidence",
    "lift",
    "term_recurrence",
    "guidance_score",
)
_HOLDOUT_FIELDS = (
    "stream",
    "academic_year",
    "term_number",
    "period_label",
    "retained_rule_count",
    "applicable_rule_count",
    "eligible_assignment_count",
    "applicable_assignment_count",
    "attained_assignment_count",
    "assignment_coverage",
    "holdout_pattern_attainment",
)


@dataclass(frozen=True, slots=True)
class HistoricalRow:
    historical_record_id: UUID
    academic_year: str
    term_number: int
    faculty_name: str
    research_faculty_key: UUID
    subject_code: str
    meeting_days: str
    time_slot: str
    room_code: str
    teacher_subject_eligible: bool
    full_stream_eligible: bool

    @property
    def period(self) -> tuple[str, int]:
        return self.academic_year, self.term_number


@dataclass(frozen=True, slots=True)
class TrainingInputs:
    rows: tuple[HistoricalRow, ...]
    holdout_periods: tuple[tuple[str, int], ...]
    source_manifest: dict[str, object]
    source_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class EvaluationInputs:
    training: TrainingInputs
    holdout_rows: tuple[HistoricalRow, ...]


@dataclass(frozen=True, slots=True)
class ExplorationResult:
    stream: PatternStream
    thresholds: MiningThresholds
    eligible_training_term_count: int
    mined_training_term_count: int
    transaction_count: int
    retained_rule_count: int
    mean_support: Decimal | None
    mean_confidence: Decimal | None
    mean_lift: Decimal | None
    mean_term_recurrence: Decimal | None
    mean_guidance_score: Decimal | None
    skipped_reason: str | None

    def as_csv_row(self) -> dict[str, object]:
        return {
            "stream": self.stream.value,
            "min_support": self.thresholds.min_support,
            "min_confidence": self.thresholds.min_confidence,
            "min_lift": self.thresholds.min_lift,
            "min_term_recurrence": self.thresholds.min_term_recurrence,
            "eligible_training_term_count": self.eligible_training_term_count,
            "mined_training_term_count": self.mined_training_term_count,
            "transaction_count": self.transaction_count,
            "retained_rule_count": self.retained_rule_count,
            "mean_support": self.mean_support,
            "mean_confidence": self.mean_confidence,
            "mean_lift": self.mean_lift,
            "mean_term_recurrence": self.mean_term_recurrence,
            "mean_guidance_score": self.mean_guidance_score,
            "skipped_reason": self.skipped_reason,
        }


@dataclass(frozen=True, slots=True)
class HoldoutResult:
    stream: PatternStream
    academic_year: str | None
    term_number: int | None
    period_label: str
    retained_rule_count: int
    applicable_rule_count: int
    eligible_assignment_count: int
    applicable_assignment_count: int
    attained_assignment_count: int
    assignment_coverage: Decimal | None
    holdout_pattern_attainment: Decimal | None

    def as_csv_row(self) -> dict[str, object]:
        return {
            "stream": self.stream.value,
            "academic_year": self.academic_year,
            "term_number": self.term_number,
            "period_label": self.period_label,
            "retained_rule_count": self.retained_rule_count,
            "applicable_rule_count": self.applicable_rule_count,
            "eligible_assignment_count": self.eligible_assignment_count,
            "applicable_assignment_count": self.applicable_assignment_count,
            "attained_assignment_count": self.attained_assignment_count,
            "assignment_coverage": self.assignment_coverage,
            "holdout_pattern_attainment": self.holdout_pattern_attainment,
        }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _analysis_hashes() -> dict[str, str]:
    return {
        "analysis_script": sha256(Path(__file__)),
        "arm_mining": sha256(Path(inspect.getfile(RuleMiner))),
        "arm_preprocessing": sha256(Path(inspect.getfile(TransactionBuilder))),
    }


def _normalized(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _research_faculty_key(faculty_name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"chronos:historical-faculty:{faculty_name}")


def _snapshot_key(academic_year: str, term_number: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"chronos:historical-term:{academic_year}:{term_number}")


def _parse_period(raw: Sequence[object]) -> tuple[str, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"holdout period must contain academic year and term: {raw!r}")
    academic_year = _normalized(raw[0])
    term_number = _integer(raw[1], "term_number")
    _validate_period(academic_year, term_number)
    return academic_year, term_number


def _validate_period(academic_year: str, term_number: int) -> None:
    match = _ACADEMIC_YEAR.fullmatch(academic_year)
    if match is None or int(match[2]) != int(match[1]) + 1:
        raise ValueError(f"academic year must use consecutive YYYY-YYYY: {academic_year!r}")
    if not isinstance(term_number, int) or isinstance(term_number, bool) or term_number not in {1, 2, 3}:
        raise ValueError("term_number must be 1, 2, or 3")


def _load_source_manifest(preprocessing_dir: Path) -> tuple[dict[str, object], str]:
    path = preprocessing_dir / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("source manifest must be a JSON object")
    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, dict):
        raise ValueError("source manifest has no output_sha256 mapping")
    for filename in ("training_records.csv", "holdout_records.csv"):
        digest = output_hashes.get(filename)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"source manifest must record a valid SHA256 for {filename}")
    raw_periods = manifest.get("holdout_periods")
    if not isinstance(raw_periods, list):
        raise ValueError("source manifest has no holdout_periods list")
    periods = tuple(_parse_period(period) for period in raw_periods)
    if len(periods) != HOLDOUT_TERM_COUNT or len(set(periods)) != HOLDOUT_TERM_COUNT:
        raise ValueError("source manifest must declare three distinct holdout terms")
    return manifest, sha256(path)


def _manifest_periods(manifest: Mapping[str, object]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(_parse_period(period) for period in manifest["holdout_periods"]))  # type: ignore[index]


def _verify_preprocessing_artifact(
    preprocessing_dir: Path,
    manifest: Mapping[str, object],
    filename: str,
) -> Path:
    output_hashes = manifest["output_sha256"]
    if not isinstance(output_hashes, Mapping) or filename not in output_hashes:
        raise ValueError(f"source manifest does not record {filename}")
    path = preprocessing_dir / filename
    actual = sha256(path)
    expected = str(output_hashes[filename])
    if actual != expected:
        raise ValueError(f"{filename} does not match its source-manifest hash")
    return path


def _read_historical_rows(path: Path) -> tuple[HistoricalRow, ...]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not _REQUIRED_RECORD_FIELDS <= set(reader.fieldnames):
            missing = sorted(_REQUIRED_RECORD_FIELDS - set(reader.fieldnames or ()))
            raise ValueError(f"{path} is missing required columns: {missing}")
        rows = tuple(_parse_historical_row(row, path, line) for line, row in enumerate(reader, 2))

    record_ids = [row.historical_record_id for row in rows]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError(f"{path} contains duplicate historical_record_id values")
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.academic_year,
                row.term_number,
                str(row.historical_record_id),
            ),
        )
    )


def _parse_historical_row(
    row: Mapping[str, str], path: Path, line: int
) -> HistoricalRow:
    try:
        record_id = UUID(_normalized(row["historical_record_id"]))
        academic_year = _normalized(row["academic_year"])
        term_number = _integer(row["term_number"], "term_number")
        _validate_period(academic_year, term_number)
        faculty_name = _normalized(row["faculty_name"]).upper()
        subject_code = _normalized(row["canonical_course_code"]).upper()
        meeting_days = _canonical_days(_normalized(row["meeting_days"]))
        start_time = _normalized(row["start_time"])
        end_time = _normalized(row["end_time"])
        room_code = _normalized(row["room_code"]).upper()
        teacher_subject_eligible = _parse_flag(row["teacher_subject_eligible"])
        full_stream_eligible = _parse_flag(row["full_stream_eligible"])
        policy = HistoricalInclusionPolicy()
        if teacher_subject_eligible != policy.is_eligible(PatternStream.TEACHER_SUBJECT, academic_year):
            raise ValueError("teacher_subject_eligible does not match the historical window")
        if full_stream_eligible != policy.is_eligible(PatternStream.TEACHER_DAY, academic_year):
            raise ValueError("full_stream_eligible does not match the historical window")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{path}:{line}: invalid historical record: {error}") from error

    required = {
        "faculty_name": faculty_name,
        "canonical_course_code": subject_code,
        "meeting_days": meeting_days,
        "room_code": room_code,
    }
    blank = [name for name, value in required.items() if not value]
    if blank:
        raise ValueError(f"{path}:{line}: blank required values: {blank}")
    if not _CLOCK_TIME.fullmatch(start_time) or not _CLOCK_TIME.fullmatch(end_time):
        raise ValueError(f"{path}:{line}: times must use 24-hour HH:MM format")
    if end_time <= start_time:
        raise ValueError(f"{path}:{line}: end_time must be later than start_time")

    return HistoricalRow(
        historical_record_id=record_id,
        academic_year=academic_year,
        term_number=term_number,
        faculty_name=faculty_name,
        research_faculty_key=_research_faculty_key(faculty_name),
        subject_code=subject_code,
        meeting_days=meeting_days,
        time_slot=f"{start_time}-{end_time}",
        room_code=room_code,
        teacher_subject_eligible=teacher_subject_eligible,
        full_stream_eligible=full_stream_eligible,
    )


def _parse_flag(value: object) -> bool:
    normalized = _normalized(value)
    if normalized not in {"0", "1"}:
        raise ValueError(f"eligibility flag must be 0 or 1, got {normalized!r}")
    return normalized == "1"


def _canonical_days(value: str) -> str:
    value = value.upper()
    days: list[str] = []
    offset = 0
    while offset < len(value):
        match = next(
            ((source, canonical) for source, canonical in _DAY_TOKENS if value.startswith(source, offset)),
            None,
        )
        if match is None or match[1] in days:
            raise ValueError(f"invalid day pattern: {value!r}")
        source, token = match
        days.append(token)
        offset += len(source)
    return "".join(sorted(days, key=_DAY_ORDER.index))


def load_training_inputs(preprocessing_dir: Path) -> TrainingInputs:
    """Load training inputs without opening holdout_records.csv."""
    manifest, manifest_hash = _load_source_manifest(preprocessing_dir)
    holdout_periods = _manifest_periods(manifest)
    training_path = _verify_preprocessing_artifact(
        preprocessing_dir, manifest, "training_records.csv"
    )
    rows = _read_historical_rows(training_path)
    training_periods = {row.period for row in rows}
    leaked_periods = sorted(training_periods & set(holdout_periods))
    if leaked_periods:
        raise ValueError(f"training records contain declared holdout periods: {leaked_periods}")
    earliest_holdout = min(holdout_periods)
    if any(period >= earliest_holdout for period in training_periods):
        raise ValueError("training periods must be strictly before the earliest holdout period")
    return TrainingInputs(rows, holdout_periods, manifest, manifest_hash)


def load_evaluation_inputs(
    preprocessing_dir: Path, training: TrainingInputs | None = None
) -> EvaluationInputs:
    if training is None:
        training = load_training_inputs(preprocessing_dir)
    holdout_path = _verify_preprocessing_artifact(
        preprocessing_dir, training.source_manifest, "holdout_records.csv"
    )
    holdout_rows = _read_historical_rows(holdout_path)
    actual_periods = tuple(sorted({row.period for row in holdout_rows}))
    if actual_periods != tuple(sorted(training.holdout_periods)):
        raise ValueError(
            "holdout records must contain exactly the three periods declared by the source manifest"
        )
    training_ids = {row.historical_record_id for row in training.rows}
    overlap = training_ids & {row.historical_record_id for row in holdout_rows}
    if overlap:
        raise ValueError("historical records cannot appear in both training and holdout")
    return EvaluationInputs(training, holdout_rows)


def _eligible(row: HistoricalRow, stream: PatternStream) -> bool:
    if stream is PatternStream.TEACHER_SUBJECT:
        return row.teacher_subject_eligible
    return row.full_stream_eligible


def build_stream_training_data(
    rows: Sequence[HistoricalRow], stream: PatternStream
) -> StreamTrainingData:
    grouped: dict[tuple[str, int], list[HistoricalRow]] = defaultdict(list)
    for row in rows:
        if _eligible(row, stream):
            grouped[row.period].append(row)

    terms = tuple(
        HistoricalTerm(
            snapshot_id=_snapshot_key(*period),
            academic_year=period[0],
            term_number=period[1],
            offerings=tuple(
                HistoricalOffering(
                    historical_record_id=row.historical_record_id,
                    faculty_id=row.research_faculty_key,
                    subject_code=row.subject_code,
                    meeting_days=row.meeting_days,
                    time_slot=row.time_slot,
                    room_code=row.room_code,
                )
                for row in sorted(grouped[period], key=lambda item: str(item.historical_record_id))
            ),
        )
        for period in sorted(grouped)
    )
    partition = HistoricalPartition(training_terms=terms, holdout_terms=())
    return StreamTrainingData(
        stream=stream,
        partition=partition,
        transactions=TransactionBuilder().build(terms, stream),
    )


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal number, got {value!r}") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal number, got {value!r}")
    return result


def _database_decimal(
    value: object, field: str, *, precision: int, scale: int
) -> Decimal:
    result = _decimal(value, field)
    quantum = Decimal(1).scaleb(-scale)
    try:
        quantized = result.quantize(quantum)
    except InvalidOperation as error:
        raise ValueError(
            f"{field} exceeds backend NUMERIC({precision},{scale})"
        ) from error
    if result != quantized:
        raise ValueError(f"{field} must have at most {scale} decimal places")
    if abs(result) >= Decimal(10) ** (precision - scale):
        raise ValueError(f"{field} exceeds backend NUMERIC({precision},{scale})")
    return result


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field} must be an exact integer, got {value!r}")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()):
        raise ValueError(f"{field} must be an exact integer, got {value!r}")
    return int(value)


def _thresholds(values: Mapping[str, object]) -> MiningThresholds:
    if not isinstance(values, Mapping) or set(values) != set(_GRID_FIELDS):
        raise ValueError(f"threshold settings must have exactly these fields: {_GRID_FIELDS}")
    thresholds = MiningThresholds(
        min_support=_database_decimal(
            values["min_support"], "min_support", precision=5, scale=4
        ),
        min_confidence=_database_decimal(
            values["min_confidence"], "min_confidence", precision=5, scale=4
        ),
        min_lift=_database_decimal(
            values["min_lift"], "min_lift", precision=8, scale=4
        ),
        min_term_recurrence=_integer(values["min_term_recurrence"], "min_term_recurrence"),
    )
    if thresholds.min_lift <= Decimal(1):
        raise ValueError(
            "research threshold exploration requires min_lift greater than 1.0"
        )
    return thresholds


def load_threshold_grid(path: Path) -> dict[PatternStream, tuple[MiningThresholds, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    _validate_stream_keys(raw)
    grid: dict[PatternStream, tuple[MiningThresholds, ...]] = {}
    for stream in PatternStream:
        values = raw[stream.value]
        if not isinstance(values, Mapping) or set(values) != set(_GRID_FIELDS):
            raise ValueError(
                f"{stream.value} grid must have exactly these fields: {_GRID_FIELDS}"
            )
        if any(not isinstance(values[field], list) or not values[field] for field in _GRID_FIELDS):
            raise ValueError(f"{stream.value} grid fields must be non-empty lists")
        combinations = {
            _thresholds(dict(zip(_GRID_FIELDS, combination, strict=True)))
            for combination in product(*(values[field] for field in _GRID_FIELDS))
        }
        grid[stream] = tuple(sorted(combinations, key=_threshold_sort_key))
    return grid


def load_selected_thresholds(path: Path) -> dict[PatternStream, MiningThresholds]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    _validate_stream_keys(raw)
    return {stream: _thresholds(raw[stream.value]) for stream in PatternStream}


def _validate_stream_keys(raw: object) -> None:
    expected = {stream.value for stream in PatternStream}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ValueError(f"configuration must have exactly these streams: {sorted(expected)}")


def _threshold_sort_key(thresholds: MiningThresholds) -> tuple[Decimal, Decimal, Decimal, int]:
    return (
        thresholds.min_support,
        thresholds.min_confidence,
        thresholds.min_lift,
        thresholds.min_term_recurrence,
    )


def explore_thresholds(
    rows: Sequence[HistoricalRow],
    grid: Mapping[PatternStream, Sequence[MiningThresholds]],
    miner: RuleMiner | None = None,
) -> tuple[ExplorationResult, ...]:
    miner = miner or RuleMiner()
    results: list[ExplorationResult] = []
    for stream in PatternStream:
        data = build_stream_training_data(rows, stream)
        for thresholds in grid[stream]:
            mined = miner.mine_stream(data, thresholds)
            results.append(
                ExplorationResult(
                    stream=stream,
                    thresholds=thresholds,
                    eligible_training_term_count=len(data.partition.training_terms),
                    mined_training_term_count=mined.training_term_count,
                    transaction_count=mined.transaction_count,
                    retained_rule_count=len(mined.rules),
                    mean_support=_mean(rule.support for rule in mined.rules),
                    mean_confidence=_mean(rule.confidence for rule in mined.rules),
                    mean_lift=_mean(rule.lift for rule in mined.rules),
                    mean_term_recurrence=_mean(
                        Decimal(rule.term_recurrence) for rule in mined.rules
                    ),
                    mean_guidance_score=_mean(
                        rule.guidance_score for rule in mined.rules
                    ),
                    skipped_reason=(
                        mined.skipped_reason.value if mined.skipped_reason else None
                    ),
                )
            )
    return tuple(results)


def _mean(values: Iterable[Decimal]) -> Decimal | None:
    materialized = tuple(values)
    if not materialized:
        return None
    return (sum(materialized, Decimal(0)) / len(materialized)).quantize(
        _METRIC_SCALE, rounding=ROUND_HALF_UP
    )


def mine_selected_rules(
    rows: Sequence[HistoricalRow],
    selected: Mapping[PatternStream, MiningThresholds],
    miner: RuleMiner | None = None,
) -> dict[PatternStream, tuple[MinedRule, ...]]:
    return {
        stream: result.rules
        for stream, result in mine_selected_results(rows, selected, miner).items()
    }


def mine_selected_results(
    rows: Sequence[HistoricalRow],
    selected: Mapping[PatternStream, MiningThresholds],
    miner: RuleMiner | None = None,
) -> dict[PatternStream, StreamMiningResult]:
    miner = miner or RuleMiner()
    return {
        stream: miner.mine_stream(
            build_stream_training_data(rows, stream), selected[stream]
        )
        for stream in PatternStream
    }


def verify_selected_was_explored(
    exploration_csv: Path,
    selected: Mapping[PatternStream, MiningThresholds],
) -> None:
    with exploration_csv.open(newline="", encoding="utf-8") as source:
        explored = {
            (
                PatternStream(row["stream"]),
                _decimal(row["min_support"], "min_support"),
                _decimal(row["min_confidence"], "min_confidence"),
                _decimal(row["min_lift"], "min_lift"),
                _integer(row["min_term_recurrence"], "min_term_recurrence"),
            )
            for row in csv.DictReader(source)
        }
    missing = [
        stream.value
        for stream, thresholds in selected.items()
        if (stream, *_threshold_sort_key(thresholds)) not in explored
    ]
    if missing:
        raise ValueError(
            f"selected thresholds were not present in the training exploration: {missing}"
        )


def _exploration_artifact(
    exploration_dir: Path,
) -> tuple[Path, dict[str, object]]:
    manifest_path = exploration_dir / "exploration_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("exploration manifest must be a JSON object")
    if manifest.get("phase") != "training_only_threshold_exploration":
        raise ValueError("exploration manifest does not describe a training-only run")
    if manifest.get("holdout_read") is not False:
        raise ValueError("exploration manifest does not prove that holdout was excluded")
    if manifest.get("holdout_present") is not False:
        raise ValueError("holdout was present during threshold exploration")

    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, Mapping):
        raise ValueError("exploration manifest has no output_sha256 mapping")
    csv_path = exploration_dir / "threshold_exploration.csv"
    if output_hashes.get(csv_path.name) != sha256(csv_path):
        raise ValueError("threshold_exploration.csv does not match its exploration manifest")
    return csv_path, manifest


def _manifest_input_hashes(
    manifest: Mapping[str, object], *, artifact: str
) -> Mapping[str, object]:
    hashes = manifest.get("input_sha256")
    if not isinstance(hashes, Mapping):
        raise ValueError(f"{artifact} has no input_sha256 mapping")
    return hashes


def _require_hash(
    hashes: Mapping[str, object], name: str, actual: str, *, artifact: str
) -> None:
    if hashes.get(name) != actual:
        raise ValueError(f"{name} does not match the {artifact}")


def freeze_selection(
    exploration_dir: Path,
    selected_thresholds: Path,
    selection_lock: Path,
) -> None:
    """Freeze one explored threshold combination per stream without holdout data."""
    _validate_output_paths(
        selection_lock,
        (exploration_dir, selected_thresholds,
         exploration_dir / "exploration_manifest.json",
         exploration_dir / "threshold_exploration.csv"),
    )
    if selection_lock.exists() and not selection_lock.is_file():
        raise ValueError("selection lock must be a regular file")
    exploration_csv, exploration_manifest = _exploration_artifact(exploration_dir)
    selected = load_selected_thresholds(selected_thresholds)
    verify_selected_was_explored(exploration_csv, selected)
    exploration_inputs = _manifest_input_hashes(
        exploration_manifest, artifact="exploration manifest"
    )
    analysis_hashes = _analysis_hashes()
    for name, actual in analysis_hashes.items():
        _require_hash(exploration_inputs, name, actual, artifact="exploration manifest")

    details = {
        "traceability": TRACEABILITY,
        "phase": "training_only_threshold_selection",
        "source_manifest_sha256": exploration_manifest.get(
            "source_manifest_sha256"
        ),
        "input_sha256": {
            "exploration_manifest.json": sha256(
                exploration_dir / "exploration_manifest.json"
            ),
            "threshold_exploration.csv": sha256(exploration_csv),
            "selected_thresholds": sha256(selected_thresholds),
            **analysis_hashes,
        },
        "selected_thresholds": _thresholds_as_json(selected),
    }
    encoded = json.dumps(details, indent=2, sort_keys=True) + "\n"
    selection_lock.parent.mkdir(parents=True, exist_ok=True)
    temporary = selection_lock.with_name(f".{selection_lock.name}-{uuid4().hex}")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        try:
            os.link(temporary, selection_lock)
        except FileExistsError:
            if selection_lock.read_text(encoding="utf-8") != encoded:
                raise ValueError(
                    "selection lock already exists with different thresholds; use a new run directory"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def _verify_selection_lock(
    selection_lock: Path,
    exploration_dir: Path,
    exploration_csv: Path,
    exploration_manifest: Mapping[str, object],
    selected_thresholds: Path,
    selected: Mapping[PatternStream, MiningThresholds],
) -> None:
    lock = json.loads(selection_lock.read_text(encoding="utf-8"))
    if not isinstance(lock, Mapping):
        raise ValueError("selection lock must be a JSON object")
    if lock.get("phase") != "training_only_threshold_selection":
        raise ValueError("selection lock does not describe a training-only selection")
    if lock.get("source_manifest_sha256") != exploration_manifest.get(
        "source_manifest_sha256"
    ):
        raise ValueError("selection lock does not match the exploration source manifest")
    if lock.get("selected_thresholds") != _thresholds_as_json(selected):
        raise ValueError("selected thresholds do not match the selection lock")

    hashes = _manifest_input_hashes(lock, artifact="selection lock")
    expected = {
        "exploration_manifest.json": sha256(
            exploration_dir / "exploration_manifest.json"
        ),
        "threshold_exploration.csv": sha256(exploration_csv),
        "selected_thresholds": sha256(selected_thresholds),
        **_analysis_hashes(),
    }
    for name, actual in expected.items():
        _require_hash(hashes, name, actual, artifact="selection lock")


def _verify_exploration_inputs(
    exploration_manifest: Mapping[str, object],
    preprocessing_dir: Path,
    training: TrainingInputs,
) -> None:
    if exploration_manifest.get("source_manifest_sha256") != (
        training.source_manifest_sha256
    ):
        raise ValueError("source_manifest.json does not match the training exploration")
    hashes = _manifest_input_hashes(
        exploration_manifest, artifact="exploration manifest"
    )
    _require_hash(
        hashes,
        "training_records.csv",
        sha256(preprocessing_dir / "training_records.csv"),
        artifact="exploration manifest",
    )
    for name, actual in _analysis_hashes().items():
        _require_hash(hashes, name, actual, artifact="exploration manifest")
    raw_periods = exploration_manifest.get("declared_holdout_periods")
    if not isinstance(raw_periods, list):
        raise ValueError("exploration manifest has no declared holdout periods")
    if tuple(sorted(_parse_period(period) for period in raw_periods)) != (
        training.holdout_periods
    ):
        raise ValueError("declared holdout periods do not match the training exploration")


def verify_frozen_inputs(
    preprocessing_dir: Path,
    exploration_dir: Path,
    selected_thresholds: Path,
    selection_lock: Path,
) -> tuple[TrainingInputs, dict[PatternStream, MiningThresholds], Path]:
    """Verify frozen training/source provenance without opening holdout data."""
    exploration_csv, exploration_manifest = _exploration_artifact(exploration_dir)
    selected = load_selected_thresholds(selected_thresholds)
    verify_selected_was_explored(exploration_csv, selected)
    _verify_selection_lock(
        selection_lock,
        exploration_dir,
        exploration_csv,
        exploration_manifest,
        selected_thresholds,
        selected,
    )
    training = load_training_inputs(preprocessing_dir)
    _verify_exploration_inputs(exploration_manifest, preprocessing_dir, training)
    return training, selected, exploration_csv


def evaluate_holdout(
    rules_by_stream: Mapping[PatternStream, Sequence[MinedRule]],
    holdout_rows: Sequence[HistoricalRow],
    holdout_periods: Sequence[tuple[str, int]],
) -> tuple[HoldoutResult, ...]:
    results: list[HoldoutResult] = []
    for stream in PatternStream:
        rules = tuple(rules_by_stream[stream])
        for period in holdout_periods:
            period_rows = tuple(row for row in holdout_rows if row.period == period)
            results.append(_evaluate_rows(stream, rules, period_rows, period))
        results.append(_evaluate_rows(stream, rules, holdout_rows, None))
    return tuple(results)


def _evaluate_rows(
    stream: PatternStream,
    rules: Sequence[MinedRule],
    rows: Sequence[HistoricalRow],
    period: tuple[str, int] | None,
) -> HoldoutResult:
    eligible_rows = tuple(row for row in rows if _eligible(row, stream))
    applicable_rules = tuple(
        rule
        for rule in rules
        if any(_rule_applies(stream, rule, row) for row in eligible_rows)
    )
    applicable_assignments = 0
    attained_assignments = 0
    for row in eligible_rows:
        row_rules = tuple(
            rule for rule in rules if _rule_applies(stream, rule, row)
        )
        if not row_rules:
            continue
        applicable_assignments += 1
        if any(_rule_is_attained(stream, rule, row) for rule in row_rules):
            attained_assignments += 1

    academic_year, term_number = period if period is not None else (None, None)
    label = (
        f"{academic_year} T{term_number}"
        if period is not None
        else "All holdout terms"
    )
    return HoldoutResult(
        stream=stream,
        academic_year=academic_year,
        term_number=term_number,
        period_label=label,
        retained_rule_count=len(rules),
        applicable_rule_count=len(applicable_rules),
        eligible_assignment_count=len(eligible_rows),
        applicable_assignment_count=applicable_assignments,
        attained_assignment_count=attained_assignments,
        assignment_coverage=_ratio(applicable_assignments, len(eligible_rows)),
        holdout_pattern_attainment=_ratio(
            attained_assignments, applicable_assignments
        ),
    )


def _rule_applies(
    stream: PatternStream, rule: MinedRule, row: HistoricalRow
) -> bool:
    if rule.faculty_id != row.research_faculty_key:
        return False
    if stream is PatternStream.TEACHER_SUBJECT_ROOM:
        return rule.subject_code == row.subject_code
    return True


def _rule_is_attained(
    stream: PatternStream, rule: MinedRule, row: HistoricalRow
) -> bool:
    if stream is PatternStream.TEACHER_SUBJECT:
        return rule.subject_code == row.subject_code
    if stream is PatternStream.TEACHER_DAY:
        return rule.meeting_days == row.meeting_days
    if stream is PatternStream.TEACHER_TIME:
        return rule.time_slot == row.time_slot
    if stream is PatternStream.TEACHER_SUBJECT_ROOM:
        return rule.room_code == row.room_code
    raise ValueError(f"unsupported pattern stream: {stream!r}")


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        _METRIC_SCALE, rounding=ROUND_HALF_UP
    )


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if value is None else value for key, value in row.items()})


def _write_transaction_matrices(
    output_dir: Path, rows: Sequence[HistoricalRow]
) -> list[str]:
    filenames: list[str] = []
    for stream in PatternStream:
        data = build_stream_training_data(rows, stream)
        items = sorted({item for transaction in data.transactions for item in transaction.items})
        period_by_snapshot = {
            term.snapshot_id: (term.academic_year, term.term_number)
            for term in data.partition.training_terms
        }
        fieldnames = [
            "snapshot_id",
            "historical_record_id",
            "academic_year",
            "term_number",
            *items,
        ]
        matrix_rows = []
        for transaction in data.transactions:
            academic_year, term_number = period_by_snapshot[transaction.snapshot_id]
            present = set(transaction.items)
            matrix_rows.append(
                {
                    "snapshot_id": transaction.snapshot_id,
                    "historical_record_id": transaction.historical_record_id,
                    "academic_year": academic_year,
                    "term_number": term_number,
                    **{item: int(item in present) for item in items},
                }
            )
        filename = f"transactions_{stream.value}.csv"
        _write_csv(output_dir / filename, fieldnames, matrix_rows)
        filenames.append(filename)
    return filenames


def _write_faculty_identity_map(path: Path, rows: Sequence[HistoricalRow]) -> None:
    identities = sorted(
        {(str(row.research_faculty_key), row.faculty_name) for row in rows}
    )
    _write_csv(
        path,
        ("research_faculty_key", "faculty_name"),
        (
            {"research_faculty_key": key, "faculty_name": name}
            for key, name in identities
        ),
    )


def _write_rules(
    path: Path,
    rules_by_stream: Mapping[PatternStream, Sequence[MinedRule]],
    faculty_names: Mapping[UUID, str],
) -> None:
    rows = []
    for stream in PatternStream:
        for rule in rules_by_stream[stream]:
            rows.append(
                {
                    "stream": stream.value,
                    "research_faculty_key": rule.faculty_id,
                    "faculty_name": faculty_names[rule.faculty_id],
                    "subject_code": rule.subject_code,
                    "room_code": rule.room_code,
                    "meeting_days": rule.meeting_days,
                    "time_slot": rule.time_slot,
                    "support": rule.support,
                    "confidence": rule.confidence,
                    "lift": rule.lift,
                    "term_recurrence": rule.term_recurrence,
                    "guidance_score": rule.guidance_score,
                }
            )
    _write_csv(path, _RULE_FIELDS, rows)


def _write_threshold_chart(path: Path, results: Sequence[ExplorationResult]) -> None:
    plt.rcParams["svg.hashsalt"] = "chronos-chr-99-thresholds"
    values = [
        [item.retained_rule_count for item in results if item.stream is stream]
        for stream in PatternStream
    ]
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.boxplot(values, tick_labels=[_stream_label(stream) for stream in PatternStream])
    axis.set_title("Retained rules across training-only threshold combinations")
    axis.set_ylabel("Retained rule count")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(
        path,
        format="svg",
        metadata={"Date": None, "Creator": TRACEABILITY},
    )
    plt.close(figure)


def _write_holdout_chart(path: Path, results: Sequence[HoldoutResult]) -> None:
    plt.rcParams["svg.hashsalt"] = "chronos-chr-99-holdout"
    labels = []
    for result in results:
        if result.stream is PatternStream.TEACHER_SUBJECT:
            labels.append(result.period_label)
    figure, axis = plt.subplots(figsize=(10, 5.5))
    for stream in PatternStream:
        stream_results = [result for result in results if result.stream is stream]
        values = [
            float(result.holdout_pattern_attainment)
            if result.holdout_pattern_attainment is not None
            else float("nan")
            for result in stream_results
        ]
        axis.plot(labels, values, marker="o", label=_stream_label(stream))
    axis.set_ylim(0, 1.05)
    axis.set_title("Historical-pattern attainment across chronological holdout terms")
    axis.set_ylabel("Attained applicable assignments")
    axis.legend(loc="best")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(
        path,
        format="svg",
        metadata={"Date": None, "Creator": TRACEABILITY},
    )
    plt.close(figure)


def _stream_label(stream: PatternStream) -> str:
    return stream.value.replace("_", "-")


def _thresholds_as_json(
    selected: Mapping[PatternStream, MiningThresholds]
) -> dict[str, dict[str, object]]:
    return {
        stream.value: {
            "min_support": str(thresholds.min_support),
            "min_confidence": str(thresholds.min_confidence),
            "min_lift": str(thresholds.min_lift),
            "min_term_recurrence": thresholds.min_term_recurrence,
        }
        for stream, thresholds in selected.items()
    }


def _write_manifest(
    output_dir: Path,
    details: dict[str, object],
    *,
    manifest_name: str,
) -> None:
    details["output_sha256"] = {
        path.name: sha256(path)
        for path in sorted(output_dir.iterdir())
        if path.name != manifest_name
    }
    (output_dir / manifest_name).write_text(
        json.dumps(details, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _validate_output_paths(output: Path, inputs: Sequence[Path]) -> None:
    if output.is_symlink():
        raise ValueError(f"output path must not be a symlink: {output}")
    for input_path in inputs:
        for destination, source in (
            (output.absolute(), input_path.absolute()),
            (output.resolve(), input_path.resolve()),
        ):
            if destination.is_relative_to(source) or source.is_relative_to(destination):
                raise ValueError(f"output path overlaps input: {input_path}")


def _validate_output_directory(
    output_dir: Path, managed_names: set[str], inputs: Sequence[Path] = ()
) -> None:
    _validate_output_paths(output_dir, inputs)
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output path must be a directory: {output_dir}")
    unknown = (
        {path.name for path in output_dir.iterdir()} - managed_names
        if output_dir.exists()
        else set()
    )
    if unknown:
        raise ValueError(f"output directory contains unmanaged files: {sorted(unknown)}")
    if output_dir.exists():
        for path in output_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"managed output entry must be a regular file: {path}")


def _replace_output_directory(
    staging_dir: Path, output_dir: Path, managed_names: set[str]
) -> None:
    _validate_output_directory(output_dir, managed_names)
    previous = output_dir.with_name(f".{output_dir.name}-previous-{uuid4().hex}")
    if output_dir.exists():
        os.replace(output_dir, previous)
    try:
        os.replace(staging_dir, output_dir)
    except OSError:
        if previous.exists():
            os.replace(previous, output_dir)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def run_exploration(
    preprocessing_dir: Path, threshold_grid: Path, output_dir: Path
) -> None:
    matrix_names = {f"transactions_{stream.value}.csv" for stream in PatternStream}
    managed = {
        "threshold_exploration.csv",
        "threshold_rule_distribution.svg",
        "faculty_identity_map.csv",
        "exploration_manifest.json",
        *matrix_names,
    }
    _validate_output_directory(
        output_dir, managed,
        (preprocessing_dir, threshold_grid, *(preprocessing_dir / name for name in
          ("training_records.csv", "holdout_records.csv", "source_manifest.json"))),
    )
    if (preprocessing_dir / "holdout_records.csv").exists():
        raise ValueError(
            "holdout_records.csv must not be present during threshold exploration"
        )
    inputs = load_training_inputs(preprocessing_dir)
    grid = load_threshold_grid(threshold_grid)
    results = explore_thresholds(inputs.rows, grid)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent, prefix=f".{output_dir.name}-"
    ) as temporary:
        staging = Path(temporary)
        _write_csv(
            staging / "threshold_exploration.csv",
            _EXPLORATION_FIELDS,
            (result.as_csv_row() for result in results),
        )
        _write_threshold_chart(staging / "threshold_rule_distribution.svg", results)
        _write_faculty_identity_map(staging / "faculty_identity_map.csv", inputs.rows)
        _write_transaction_matrices(staging, inputs.rows)
        _write_manifest(
            staging,
            {
                "traceability": TRACEABILITY,
                "phase": "training_only_threshold_exploration",
                "holdout_read": False,
                "holdout_present": False,
                "source_manifest_sha256": inputs.source_manifest_sha256,
                "input_sha256": {
                    "training_records.csv": sha256(
                        preprocessing_dir / "training_records.csv"
                    ),
                    "threshold_grid": sha256(threshold_grid),
                    **_analysis_hashes(),
                },
                "declared_holdout_periods": inputs.holdout_periods,
                "training_record_count": len(inputs.rows),
                "threshold_combination_count": len(results),
            },
            manifest_name="exploration_manifest.json",
        )
        _replace_output_directory(staging, output_dir, managed)


def run_evaluation(
    preprocessing_dir: Path,
    exploration_dir: Path,
    selected_thresholds: Path,
    selection_lock: Path,
    output_dir: Path,
) -> None:
    managed = {
        "selected_rules.csv",
        "holdout_evaluation.csv",
        "holdout_pattern_attainment.svg",
        "evaluation_manifest.json",
    }
    _validate_output_directory(
        output_dir, managed,
        (preprocessing_dir, exploration_dir, selected_thresholds, selection_lock,
         *(preprocessing_dir / name for name in
           ("training_records.csv", "holdout_records.csv", "source_manifest.json")),
         exploration_dir / "exploration_manifest.json",
         exploration_dir / "threshold_exploration.csv"),
    )
    training, selected, exploration_csv = verify_frozen_inputs(
        preprocessing_dir,
        exploration_dir,
        selected_thresholds,
        selection_lock,
    )
    inputs = load_evaluation_inputs(preprocessing_dir, training)
    mined_by_stream = mine_selected_results(inputs.training.rows, selected)
    rules_by_stream = {
        stream: result.rules for stream, result in mined_by_stream.items()
    }
    results = evaluate_holdout(
        rules_by_stream,
        inputs.holdout_rows,
        inputs.training.holdout_periods,
    )
    faculty_names = {
        row.research_faculty_key: row.faculty_name
        for row in (*inputs.training.rows, *inputs.holdout_rows)
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent, prefix=f".{output_dir.name}-"
    ) as temporary:
        staging = Path(temporary)
        _write_rules(staging / "selected_rules.csv", rules_by_stream, faculty_names)
        _write_csv(
            staging / "holdout_evaluation.csv",
            _HOLDOUT_FIELDS,
            (result.as_csv_row() for result in results),
        )
        _write_holdout_chart(staging / "holdout_pattern_attainment.svg", results)
        _write_manifest(
            staging,
            {
                "traceability": TRACEABILITY,
                "phase": "frozen_threshold_holdout_evaluation",
                "mined_at": datetime.now(timezone.utc).isoformat(),
                "source_manifest_sha256": inputs.training.source_manifest_sha256,
                "input_sha256": {
                    "training_records.csv": sha256(
                        preprocessing_dir / "training_records.csv"
                    ),
                    "holdout_records.csv": sha256(
                        preprocessing_dir / "holdout_records.csv"
                    ),
                    "exploration_manifest.json": sha256(
                        exploration_dir / "exploration_manifest.json"
                    ),
                    "threshold_exploration.csv": sha256(exploration_csv),
                    "selected_thresholds": sha256(selected_thresholds),
                    "selection_lock.json": sha256(selection_lock),
                    **_analysis_hashes(),
                },
                "holdout_periods": inputs.training.holdout_periods,
                "training_record_count": len(inputs.training.rows),
                "holdout_record_count": len(inputs.holdout_rows),
                "selected_thresholds": _thresholds_as_json(selected),
                "mined_training_term_counts": {
                    stream.value: mined_by_stream[stream].training_term_count
                    for stream in PatternStream
                },
                "retained_rule_counts": {
                    stream.value: len(rules_by_stream[stream])
                    for stream in PatternStream
                },
            },
            manifest_name="evaluation_manifest.json",
        )
        _replace_output_directory(staging, output_dir, managed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore = subparsers.add_parser(
        "explore", help="explore thresholds using training records only"
    )
    explore.add_argument("--preprocessing-dir", type=Path, required=True)
    explore.add_argument("--threshold-grid", type=Path, required=True)
    explore.add_argument("--output-dir", type=Path, required=True)

    freeze = subparsers.add_parser(
        "freeze", help="lock a training-only selection before holdout access"
    )
    freeze.add_argument("--exploration-dir", type=Path, required=True)
    freeze.add_argument("--selected-thresholds", type=Path, required=True)
    freeze.add_argument("--selection-lock", type=Path, required=True)

    for command, help_text in (
        ("verify", "verify frozen inputs before holdout access"),
        ("evaluate", "evaluate frozen, previously explored thresholds"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("--preprocessing-dir", type=Path, required=True)
        subparser.add_argument("--exploration-dir", type=Path, required=True)
        subparser.add_argument("--selected-thresholds", type=Path, required=True)
        subparser.add_argument("--selection-lock", type=Path, required=True)
        if command == "evaluate":
            subparser.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "explore":
        run_exploration(
            args.preprocessing_dir,
            args.threshold_grid,
            args.output_dir,
        )
    elif args.command == "freeze":
        freeze_selection(
            args.exploration_dir,
            args.selected_thresholds,
            args.selection_lock,
        )
    elif args.command == "verify":
        verify_frozen_inputs(
            args.preprocessing_dir,
            args.exploration_dir,
            args.selected_thresholds,
            args.selection_lock,
        )
    else:
        run_evaluation(
            args.preprocessing_dir,
            args.exploration_dir,
            args.selected_thresholds,
            args.selection_lock,
            args.output_dir,
        )


if __name__ == "__main__":
    main()
