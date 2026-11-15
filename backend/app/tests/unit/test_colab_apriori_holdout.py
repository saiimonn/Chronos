"""CHR-99 / CHR-COLAB-003 Apriori exploration and holdout tests."""

import ast
import csv
from decimal import Decimal
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.services.arm.preprocessing import PatternStream


SCRIPT = (
    Path(__file__).parents[3]
    / "scripts"
    / "colab_prototype"
    / "explore_apriori_holdout.py"
)
SPEC = importlib.util.spec_from_file_location("colab_apriori_holdout", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)

RECORD_FIELDS = (
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
)
HOLDOUT_PERIODS = (
    ("2024-2025", 1),
    ("2024-2025", 2),
    ("2024-2025", 3),
)


def record(
    number: int,
    academic_year: str,
    term_number: int,
    *,
    faculty: str,
    subject: str,
    days: str,
    start: str,
    end: str,
    room: str,
) -> dict[str, object]:
    return {
        "historical_record_id": str(
            UUID(f"00000000-0000-0000-0000-{number:012d}")
        ),
        "academic_year": academic_year,
        "term_number": term_number,
        "canonical_course_code": subject,
        "faculty_name": faculty,
        "meeting_days": days,
        "start_time": start,
        "end_time": end,
        "room_code": room,
        "teacher_subject_eligible": 1,
        "full_stream_eligible": 1,
    }


def training_records() -> list[dict[str, object]]:
    rows = []
    number = 1
    for academic_year, term_number in (("2023-2024", 1), ("2023-2024", 2)):
        rows.extend(
            [
                record(
                    number,
                    academic_year,
                    term_number,
                    faculty="FACULTY A",
                    subject="SUBJ-A",
                    days="MWF",
                    start="08:00",
                    end="09:30",
                    room="ROOM-A",
                ),
                record(
                    number + 1,
                    academic_year,
                    term_number,
                    faculty="FACULTY B",
                    subject="SUBJ-B",
                    days="TTh",
                    start="10:00",
                    end="11:30",
                    room="ROOM-B",
                ),
            ]
        )
        number += 2
    return rows


def holdout_records() -> list[dict[str, object]]:
    return [
        record(
            101,
            "2024-2025",
            1,
            faculty="FACULTY A",
            subject="SUBJ-A",
            days="MWF",
            start="08:00",
            end="09:30",
            room="ROOM-A",
        ),
        record(
            102,
            "2024-2025",
            1,
            faculty="FACULTY B",
            subject="SUBJ-B",
            days="TTh",
            start="10:00",
            end="11:30",
            room="ROOM-B",
        ),
        record(
            103,
            "2024-2025",
            2,
            faculty="FACULTY A",
            subject="SUBJ-X",
            days="MWF",
            start="08:00",
            end="09:30",
            room="ROOM-X",
        ),
        record(
            104,
            "2024-2025",
            2,
            faculty="FACULTY B",
            subject="SUBJ-B",
            days="TTh",
            start="10:00",
            end="11:30",
            room="ROOM-B",
        ),
        record(
            105,
            "2024-2025",
            3,
            faculty="FACULTY C",
            subject="SUBJ-A",
            days="MWF",
            start="08:00",
            end="09:30",
            room="ROOM-A",
        ),
    ]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=RECORD_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_preprocessing_artifacts(
    path: Path,
    *,
    include_holdout: bool = True,
    holdout_hash: str | None = None,
) -> None:
    path.mkdir()
    training_path = path / "training_records.csv"
    write_csv(training_path, training_records())
    hashes = {"training_records.csv": analysis.sha256(training_path)}
    if include_holdout:
        holdout_path = path / "holdout_records.csv"
        write_csv(holdout_path, holdout_records())
        hashes["holdout_records.csv"] = analysis.sha256(holdout_path)
    else:
        synthetic = io.StringIO(newline="")
        writer = csv.DictWriter(synthetic, fieldnames=RECORD_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(holdout_records())
        hashes["holdout_records.csv"] = holdout_hash or hashlib.sha256(
            synthetic.getvalue().encode("utf-8")
        ).hexdigest()
    (path / "source_manifest.json").write_text(
        json.dumps(
            {
                "holdout_periods": [list(period) for period in HOLDOUT_PERIODS],
                "output_sha256": hashes,
            }
        ),
        encoding="utf-8",
    )


def threshold_values() -> dict[str, object]:
    return {
        "min_support": "0.25",
        "min_confidence": "0.5",
        "min_lift": "1.1",
        "min_term_recurrence": 1,
    }


def write_grid(path: Path, **overrides: list[object]) -> None:
    values = threshold_values()
    path.write_text(
        json.dumps(
            {
                stream.value: {key: [value] for key, value in values.items()} | overrides
                for stream in PatternStream
            }
        ),
        encoding="utf-8",
    )


def write_selected(path: Path, **overrides: object) -> None:
    values = threshold_values() | overrides
    path.write_text(
        json.dumps({stream.value: values for stream in PatternStream}),
        encoding="utf-8",
    )


def selected_thresholds() -> dict:
    values = analysis._thresholds(threshold_values())
    return {stream: values for stream in PatternStream}


def run_training_phases(
    preprocessing: Path,
    grid: Path,
    selected: Path,
    exploration: Path,
    selection_lock: Path,
) -> None:
    holdout_path = preprocessing / "holdout_records.csv"
    holdout_bytes = holdout_path.read_bytes()
    holdout_path.unlink()
    try:
        analysis.run_exploration(preprocessing, grid, exploration)
        analysis.freeze_selection(exploration, selected, selection_lock)
    finally:
        holdout_path.write_bytes(holdout_bytes)


def test_exploration_does_not_read_or_require_the_holdout_file(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    output = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)

    analysis.run_exploration(preprocessing, grid, output)

    manifest = json.loads((output / "exploration_manifest.json").read_text())
    assert manifest["phase"] == "training_only_threshold_exploration"
    assert manifest["holdout_read"] is False
    assert manifest["holdout_present"] is False
    assert manifest["threshold_combination_count"] == 4
    assert not (preprocessing / "holdout_records.csv").exists()
    assert {
        f"transactions_{stream.value}.csv" for stream in PatternStream
    } <= {path.name for path in output.iterdir()}


def test_threshold_exploration_is_reproducible_and_keeps_four_streams(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid_path = tmp_path / "grid.json"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid_path)
    inputs = analysis.load_training_inputs(preprocessing)
    grid = analysis.load_threshold_grid(grid_path)

    first = analysis.explore_thresholds(inputs.rows, grid)
    second = analysis.explore_thresholds(tuple(reversed(inputs.rows)), grid)

    assert first == second
    assert [result.stream for result in first] == list(PatternStream)
    assert all(result.retained_rule_count == 2 for result in first)
    assert all(result.mean_confidence == Decimal("1.0000") for result in first)
    assert all(result.mean_lift == Decimal("2.0000") for result in first)


@pytest.mark.parametrize(
    ("academic_year", "term_number", "message"),
    [
        ("2025-2026", 1, "strictly before the earliest holdout"),
        ("2024-2025", 4, "term_number must be 1, 2, or 3"),
        ("2024-2025", 1, "contain declared holdout periods"),
    ],
)
def test_training_periods_must_precede_holdout(
    tmp_path: Path, academic_year: str, term_number: int, message: str
):
    preprocessing = tmp_path / "preprocessing"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    rows = training_records()
    rows[0].update(academic_year=academic_year, term_number=term_number)
    training_path = preprocessing / "training_records.csv"
    write_csv(training_path, rows)
    manifest_path = preprocessing / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_sha256"]["training_records.csv"] = analysis.sha256(training_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        analysis.load_training_inputs(preprocessing)


def test_transaction_matrix_uses_canonical_stream_shapes(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    output = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)

    analysis.run_exploration(preprocessing, grid, output)

    with (output / "transactions_teacher_subject_room.csv").open(
        newline="", encoding="utf-8"
    ) as source:
        rows = list(csv.DictReader(source))
    item_columns = {
        key
        for key in rows[0]
        if key
        not in {
            "snapshot_id",
            "historical_record_id",
            "academic_year",
            "term_number",
        }
    }
    assert any(item.startswith("faculty=") for item in item_columns)
    assert {"subject=SUBJ-A", "subject=SUBJ-B"} <= item_columns
    assert {"room=ROOM-A", "room=ROOM-B"} <= item_columns


def test_holdout_reports_per_term_and_weighted_aggregate_attainment(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    write_preprocessing_artifacts(preprocessing)
    inputs = analysis.load_evaluation_inputs(preprocessing)
    rules = analysis.mine_selected_rules(inputs.training.rows, selected_thresholds())

    results = analysis.evaluate_holdout(
        rules, inputs.holdout_rows, inputs.training.holdout_periods
    )
    subjects = [
        result
        for result in results
        if result.stream is PatternStream.TEACHER_SUBJECT
    ]

    assert len(subjects) == 4
    first, second, third, aggregate = subjects
    assert (first.applicable_assignment_count, first.attained_assignment_count) == (2, 2)
    assert first.holdout_pattern_attainment == Decimal("1.0000")
    assert (second.applicable_assignment_count, second.attained_assignment_count) == (2, 1)
    assert second.holdout_pattern_attainment == Decimal("0.5000")
    assert third.applicable_rule_count == 0
    assert third.applicable_assignment_count == 0
    assert third.holdout_pattern_attainment is None
    assert aggregate.eligible_assignment_count == 5
    assert aggregate.applicable_rule_count == 2
    assert aggregate.applicable_assignment_count == 4
    assert aggregate.attained_assignment_count == 3
    assert aggregate.assignment_coverage == Decimal("0.8000")
    assert aggregate.holdout_pattern_attainment == Decimal("0.7500")


def test_subject_room_applicability_includes_the_subject_antecedent(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    write_preprocessing_artifacts(preprocessing)
    inputs = analysis.load_evaluation_inputs(preprocessing)
    rules = analysis.mine_selected_rules(inputs.training.rows, selected_thresholds())

    aggregate = next(
        result
        for result in analysis.evaluate_holdout(
            rules, inputs.holdout_rows, inputs.training.holdout_periods
        )
        if result.stream is PatternStream.TEACHER_SUBJECT_ROOM
        and result.academic_year is None
    )

    assert aggregate.applicable_assignment_count == 3
    assert aggregate.attained_assignment_count == 3
    assert aggregate.holdout_pattern_attainment == Decimal("1.0000")


def test_research_grid_requires_lift_strictly_above_one(tmp_path: Path):
    grid = tmp_path / "grid.json"
    values = threshold_values() | {"min_lift": "1.0"}
    grid.write_text(
        json.dumps(
            {
                stream.value: {key: [value] for key, value in values.items()}
                for stream in PatternStream
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="greater than 1.0"):
        analysis.load_threshold_grid(grid)


def test_evaluation_rejects_a_selection_that_was_not_explored(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid)
    write_selected(selected, min_support="0.5")
    holdout_path = preprocessing / "holdout_records.csv"
    holdout_bytes = holdout_path.read_bytes()
    holdout_path.unlink()
    analysis.run_exploration(preprocessing, grid, exploration)
    holdout_path.write_bytes(holdout_bytes)

    with pytest.raises(ValueError, match="not present"):
        analysis.run_evaluation(
            preprocessing,
            exploration,
            selected,
            tmp_path / "missing-selection-lock.json",
            tmp_path / "evaluation",
        )


def test_evaluation_writes_auditable_outputs_for_frozen_thresholds(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    evaluation = tmp_path / "evaluation"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid)
    write_selected(selected)
    run_training_phases(preprocessing, grid, selected, exploration, selection_lock)

    analysis.run_evaluation(
        preprocessing, exploration, selected, selection_lock, evaluation
    )

    manifest = json.loads((evaluation / "evaluation_manifest.json").read_text())
    assert manifest["phase"] == "frozen_threshold_holdout_evaluation"
    assert manifest["holdout_record_count"] == 5
    assert manifest["retained_rule_counts"] == {
        stream.value: 2 for stream in PatternStream
    }
    assert manifest["mined_training_term_counts"] == {
        stream.value: 2 for stream in PatternStream
    }
    assert manifest["input_sha256"]["selection_lock.json"] == analysis.sha256(
        selection_lock
    )
    assert set(manifest["output_sha256"]) == {
        "selected_rules.csv",
        "holdout_evaluation.csv",
        "holdout_pattern_attainment.svg",
    }
    expected_hashes = {
        "analysis_script": analysis.sha256(SCRIPT),
        "arm_mining": analysis.sha256(SCRIPT.parents[2] / "app/services/arm/mining.py"),
        "arm_preprocessing": analysis.sha256(
            SCRIPT.parents[2] / "app/services/arm/preprocessing.py"
        ),
    }
    assert analysis._analysis_hashes() == expected_hashes
    for manifest_path in (
        exploration / "exploration_manifest.json",
        selection_lock,
        evaluation / "evaluation_manifest.json",
    ):
        hashes = json.loads(manifest_path.read_text(encoding="utf-8"))["input_sha256"]
        assert {name: hashes[name] for name in expected_hashes} == expected_hashes
    with (evaluation / "holdout_evaluation.csv").open(
        newline="", encoding="utf-8"
    ) as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == len(PatternStream) * (len(HOLDOUT_PERIODS) + 1)


def test_evaluation_rejects_a_holdout_file_changed_after_preprocessing(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    write_preprocessing_artifacts(preprocessing)
    with (preprocessing / "holdout_records.csv").open("a", encoding="utf-8") as target:
        target.write("tampered\n")

    with pytest.raises(ValueError, match="source-manifest hash"):
        analysis.load_evaluation_inputs(preprocessing)


def test_exploration_rejects_a_present_holdout_file(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid)

    with pytest.raises(ValueError, match="must not be present"):
        analysis.run_exploration(preprocessing, grid, tmp_path / "exploration")


def test_selection_lock_precedes_holdout_access(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    analysis.freeze_selection(exploration, selected, selection_lock)
    selection_lock.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="selection lock"):
        analysis.run_evaluation(
            preprocessing,
            exploration,
            selected,
            selection_lock,
            tmp_path / "evaluation",
        )


def test_evaluation_rejects_training_other_than_the_explored_source(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    replacement = tmp_path / "replacement"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing)
    write_preprocessing_artifacts(replacement)
    replacement_training = replacement / "training_records.csv"
    changed_rows = training_records()
    changed_rows[0]["canonical_course_code"] = "SUBJ-CHANGED"
    write_csv(replacement_training, changed_rows)
    replacement_manifest = replacement / "source_manifest.json"
    manifest = json.loads(replacement_manifest.read_text(encoding="utf-8"))
    manifest["output_sha256"]["training_records.csv"] = analysis.sha256(
        replacement_training
    )
    replacement_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    write_grid(grid)
    write_selected(selected)
    run_training_phases(preprocessing, grid, selected, exploration, selection_lock)
    (replacement / "holdout_records.csv").unlink()

    with pytest.raises(ValueError, match="source_manifest.json"):
        analysis.run_evaluation(
            replacement,
            exploration,
            selected,
            selection_lock,
            tmp_path / "evaluation",
        )


def test_verify_frozen_inputs_never_opens_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    analysis.freeze_selection(exploration, selected, selection_lock)
    original_open = Path.open

    def training_only_open(path: Path, *args, **kwargs):
        assert path.name != "holdout_records.csv", "verification must not open holdout"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", training_only_open)
    training, thresholds, exploration_csv = analysis.verify_frozen_inputs(
        preprocessing, exploration, selected, selection_lock
    )

    assert len(training.rows) == len(training_records())
    assert thresholds == selected_thresholds()
    assert exploration_csv == exploration / "threshold_exploration.csv"
    assert not (preprocessing / "holdout_records.csv").exists()


@pytest.mark.parametrize("source_name", ["analysis_script", "arm_mining", "arm_preprocessing"])
@pytest.mark.parametrize("phase", ["freeze", "verify", "evaluate"])
def test_changed_analysis_sources_are_rejected_before_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_name: str, phase: str
):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    evaluation = tmp_path / "evaluation"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    if phase != "freeze":
        analysis.freeze_selection(exploration, selected, selection_lock)
    changed_hashes = analysis._analysis_hashes() | {source_name: "0" * 64}
    monkeypatch.setattr(analysis, "_analysis_hashes", lambda: changed_hashes)

    with pytest.raises(ValueError, match=source_name):
        if phase == "freeze":
            analysis.freeze_selection(exploration, selected, selection_lock)
        elif phase == "verify":
            analysis.verify_frozen_inputs(
                preprocessing, exploration, selected, selection_lock
            )
        else:
            analysis.run_evaluation(
                preprocessing, exploration, selected, selection_lock, evaluation
            )
    assert not evaluation.exists()

    if phase == "verify":
        lock = json.loads(selection_lock.read_text(encoding="utf-8"))
        lock["input_sha256"][source_name] = changed_hashes[source_name]
        selection_lock.write_text(json.dumps(lock), encoding="utf-8")
        with pytest.raises(ValueError, match=f"{source_name}.*exploration manifest"):
            analysis.verify_frozen_inputs(
                preprocessing, exploration, selected, selection_lock
            )


def test_freeze_selection_is_idempotent_and_rejects_changed_selection(tmp_path: Path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    selection_lock = tmp_path / "selection-lock.json"
    exploration = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid, min_support=["0.25", "0.5"])
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    analysis.freeze_selection(exploration, selected, selection_lock)
    original = selection_lock.read_bytes()
    original_inode = selection_lock.stat().st_ino

    analysis.freeze_selection(exploration, selected, selection_lock)
    assert selection_lock.read_bytes() == original
    assert selection_lock.stat().st_ino == original_inode
    write_selected(selected, min_support="0.5")
    with pytest.raises(ValueError, match="selection lock already exists"):
        analysis.freeze_selection(exploration, selected, selection_lock)
    assert selection_lock.read_bytes() == original
    assert selection_lock.stat().st_ino == original_inode
    assert not list(tmp_path.glob(".selection-lock.json-*"))


@pytest.mark.parametrize("identical", [True, False])
def test_freeze_selection_does_not_overwrite_a_competing_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identical: bool
):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    expected_lock = tmp_path / "expected-lock.json"
    selection_lock = tmp_path / "selection-lock.json"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    analysis.freeze_selection(exploration, selected, expected_lock)
    encoded = expected_lock.read_text(encoding="utf-8")
    competing = encoded if identical else encoded.replace('"0.25"', '"0.5"')
    original_link = os.link
    published_inodes = []

    def competing_link(source: Path, destination: Path):
        destination.write_text(competing, encoding="utf-8")
        published_inodes.append(destination.stat().st_ino)
        original_link(source, destination)

    monkeypatch.setattr(analysis.os, "link", competing_link)
    if identical:
        analysis.freeze_selection(exploration, selected, selection_lock)
    else:
        with pytest.raises(ValueError, match="selection lock already exists"):
            analysis.freeze_selection(exploration, selected, selection_lock)
    assert len(published_inodes) == 1
    assert selection_lock.read_text(encoding="utf-8") == competing
    assert selection_lock.stat().st_ino == published_inodes[0]
    assert not list(tmp_path.glob(".selection-lock.json-*"))


def test_standalone_import_and_cli_without_database_or_dotenv(tmp_path: Path):
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("database_url", None)
    env["PYTHONPATH"] = str(SCRIPT.parents[2])
    assert not (tmp_path / ".env").exists()
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.services.arm; "
            "from app.services.arm import RuleMiner, TransactionBuilder; "
            "assert 'app.services.arm.providers' not in sys.modules; "
            "assert 'app.services.arm.service' not in sys.modules; "
            "assert 'app.core.config' not in sys.modules; "
            "assert 'app.db.session' not in sys.modules",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert imported.returncode == 0, imported.stderr

    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    selection_lock = tmp_path / "selection-lock.json"
    evaluation = tmp_path / "evaluation"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid)
    write_selected(selected)
    holdout_path = preprocessing / "holdout_records.csv"
    holdout_bytes = holdout_path.read_bytes()
    holdout_path.unlink()

    commands = [
        [
            "explore", "--preprocessing-dir", str(preprocessing),
            "--threshold-grid", str(grid), "--output-dir", str(exploration),
        ],
        [
            "freeze", "--exploration-dir", str(exploration),
            "--selected-thresholds", str(selected),
            "--selection-lock", str(selection_lock),
        ],
        [
            "verify", "--preprocessing-dir", str(preprocessing),
            "--exploration-dir", str(exploration),
            "--selected-thresholds", str(selected),
            "--selection-lock", str(selection_lock),
        ],
        [
            "evaluate", "--preprocessing-dir", str(preprocessing),
            "--exploration-dir", str(exploration),
            "--selected-thresholds", str(selected),
            "--selection-lock", str(selection_lock),
            "--output-dir", str(evaluation),
        ],
    ]
    for command in commands:
        if command[0] == "evaluate":
            holdout_path.write_bytes(holdout_bytes)
        completed = subprocess.run(
            [
                sys.executable, "-m", "scripts.colab_prototype.explore_apriori_holdout",
                *command,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        if command[0] == "verify":
            assert not holdout_path.exists()
            assert not evaluation.exists()
    assert (evaluation / "evaluation_manifest.json").is_file()


def test_notebook_rejects_changed_selection_before_any_holdout_request(tmp_path: Path):
    notebook_path = SCRIPT.with_name("CHR_COLAB_003_apriori_holdout_evaluation.ipynb")
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    for cell in code_cells:
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        compile("".join(cell["source"]), str(notebook_path), "exec")
    bootstrap = ast.parse("".join(code_cells[0]["source"]))
    fetch = next(
        node for node in bootstrap.body
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_github"
    )
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    lock = tmp_path / "selection_lock.json"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    write_grid(grid)
    write_selected(selected)
    analysis.run_exploration(preprocessing, grid, exploration)
    analysis.freeze_selection(exploration, selected, lock)
    write_selected(selected, min_support="0.5")

    def verify(command, **kwargs):
        assert command[1] == "verify"
        assert kwargs["check"] is True
        analysis.verify_frozen_inputs(preprocessing, exploration, selected, lock)

    def unexpected_authentication(*args, **kwargs):
        pytest.fail("holdout authentication must not start after failed preflight")

    holdout = "dataset/preprocessed/v1/holdout_records.csv"
    namespace = {
        "repository": "saiimonn/Chronos", "commit": "a" * 40,
        "dataset_version": "v1", "holdout_path": holdout,
        "metadata": {"repository": "saiimonn/Chronos", "code_commit": "a" * 40,
                     "dataset_version": "v1"},
        "destinations": {holdout: preprocessing / "holdout_records.csv"},
        "selection_lock": lock, "lock_downloaded_sha256": analysis.sha256(lock),
        "preprocessing_dir": preprocessing, "exploration_dir": exploration,
        "selected_thresholds": selected, "backend_dir": SCRIPT.parents[2],
        "hashlib": hashlib, "cli": ["test-cli"],
        "run": verify, "getpass": unexpected_authentication,
    }
    exec(compile(ast.Module(body=[fetch], type_ignores=[]), str(notebook_path), "exec"), namespace)
    with pytest.raises(ValueError, match="not present"):
        namespace["fetch_github"]((holdout,))
    assert not (preprocessing / "holdout_records.csv").exists()


@pytest.mark.parametrize("value", [True, False, 1.9, 1.0, "1.9", None, 0, -1])
@pytest.mark.parametrize("loader", ["grid", "selected"])
def test_recurrence_requires_an_exact_positive_integer(tmp_path, value, loader):
    path = tmp_path / "thresholds.json"
    if loader == "grid":
        write_grid(path, min_term_recurrence=[value])
        load = analysis.load_threshold_grid
    else:
        write_selected(path, min_term_recurrence=value)
        load = analysis.load_selected_thresholds
    with pytest.raises(ValueError, match="min_term_recurrence"):
        load(path)


@pytest.mark.parametrize("field", ["min_support", "min_confidence", "min_lift"])
@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN", "sNaN"])
def test_threshold_decimals_must_be_finite(tmp_path, field, value):
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    write_grid(grid, **{field: [value]})
    write_selected(selected, **{field: value})
    for load, path in ((analysis.load_threshold_grid, grid),
                       (analysis.load_selected_thresholds, selected)):
        with pytest.raises(ValueError, match=f"{field}.*finite"):
            load(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_support", "0.25001"),
        ("min_confidence", "0.50001"),
        ("min_lift", "1.10001"),
        ("min_lift", "10000"),
    ],
)
def test_threshold_decimals_must_fit_backend_precision(tmp_path, field, value):
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    write_grid(grid, **{field: [value]})
    write_selected(selected, **{field: value})
    for load, path in (
        (analysis.load_threshold_grid, grid),
        (analysis.load_selected_thresholds, selected),
    ):
        with pytest.raises(ValueError, match=f"{field}.*(?:decimal places|NUMERIC)"):
            load(path)


@pytest.mark.parametrize("value", [True, False, 1.9, 1.0, "1.9", None, 0, 4, 99])
def test_periods_require_supported_exact_integer_terms(value):
    with pytest.raises(ValueError, match="term_number"):
        analysis._parse_period(["2024-2025", value])
    with pytest.raises(ValueError, match="term_number"):
        analysis._validate_period("2024-2025", value)
    row = {key: str(item) for key, item in training_records()[0].items()}
    row["term_number"] = value
    with pytest.raises(ValueError, match="term_number"):
        analysis._parse_historical_row(row, Path("synthetic.csv"), 2)


@pytest.mark.parametrize(
    ("year", "subject", "full"),
    [("2010-2011", "1", "1"), ("2023-2024", "0", "1"),
     ("2023-2024", "1", "0"), ("2022-2023", "1", "1")],
)
def test_eligibility_flags_must_match_historical_policy(year, subject, full):
    row = {key: str(item) for key, item in training_records()[0].items()}
    row.update(academic_year=year, teacher_subject_eligible=subject, full_stream_eligible=full)
    with pytest.raises(ValueError, match="historical window"):
        analysis._parse_historical_row(row, Path("synthetic.csv"), 2)


@pytest.mark.parametrize(
    ("year", "subject", "full"),
    [("2017-2018", "0", "0"), ("2018-2019", "1", "0"),
     ("2022-2023", "1", "0"), ("2023-2024", "1", "1")],
)
def test_eligibility_window_boundaries(year, subject, full):
    raw = {key: str(item) for key, item in training_records()[0].items()}
    raw.update(academic_year=year, teacher_subject_eligible=subject, full_stream_eligible=full)
    row = analysis._parse_historical_row(raw, Path("synthetic.csv"), 2)
    for stream in PatternStream:
        expected = subject if stream is PatternStream.TEACHER_SUBJECT else full
        assert len(analysis.build_stream_training_data((row,), stream).transactions) == int(expected)


@pytest.mark.parametrize("days", ["MM", "NOTADAY", "ThTH", "SaSat", "SuSun"])
def test_invalid_or_duplicate_meeting_days_are_rejected(days):
    row = {key: str(item) for key, item in training_records()[0].items()}
    row["meeting_days"] = days
    with pytest.raises(ValueError, match="invalid day pattern"):
        analysis._parse_historical_row(row, Path("synthetic.csv"), 2)


@pytest.mark.parametrize("days", ["WFM", "tth", "FSat", "SUN", "SuThWTSaM"])
def test_day_normalization_matches_preprocessor(days):
    from scripts.colab_prototype.preprocess_historical_dataset import canonical_days

    row = {key: str(item) for key, item in training_records()[0].items()}
    row["meeting_days"] = days
    parsed = analysis._parse_historical_row(row, Path("synthetic.csv"), 2)
    assert parsed.meeting_days == canonical_days(days)


@pytest.mark.parametrize("filename", ["training_records.csv", "holdout_records.csv"])
@pytest.mark.parametrize("digest", [None, "", "not-a-digest", "a" * 63, "g" * 64, 123])
def test_source_manifest_requires_both_hash_commitments_before_artifact_reads(
    tmp_path, monkeypatch, filename, digest
):
    preprocessing = tmp_path / "preprocessing"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    path = preprocessing / "source_manifest.json"
    manifest = json.loads(path.read_text())
    if digest is None:
        manifest["output_sha256"].pop(filename)
    else:
        manifest["output_sha256"][filename] = digest
    path.write_text(json.dumps(manifest))
    original_open = Path.open

    def manifest_only_open(path, *args, **kwargs):
        assert path.name == "source_manifest.json", "invalid commitments must fail before CSV access"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", manifest_only_open)
    with pytest.raises(ValueError, match=f"valid SHA256 for {filename}"):
        analysis.load_training_inputs(preprocessing)


def test_holdout_order_is_canonical_without_changing_source_manifest(tmp_path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    lock = tmp_path / "lock.json"
    output = tmp_path / "evaluation"
    write_preprocessing_artifacts(preprocessing)
    path = preprocessing / "source_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["holdout_periods"] = [manifest["holdout_periods"][i] for i in (2, 0, 1)]
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    write_grid(grid)
    write_selected(selected)
    run_training_phases(preprocessing, grid, selected, exploration, lock)
    analysis.run_evaluation(preprocessing, exploration, selected, lock, output)
    training = analysis.load_training_inputs(preprocessing)
    assert training.holdout_periods == HOLDOUT_PERIODS
    assert path.read_bytes() == original
    assert training.source_manifest_sha256 == hashlib.sha256(original).hexdigest()
    with (output / "holdout_evaluation.csv").open() as source:
        rows = list(csv.DictReader(source))
    assert [row["term_number"] for row in rows if row["stream"] == "teacher_subject"] == ["1", "2", "3", ""]


def test_training_only_fixture_commits_to_the_same_synthetic_holdout(tmp_path):
    training_only = tmp_path / "training_only"
    complete = tmp_path / "complete"
    write_preprocessing_artifacts(training_only, include_holdout=False)
    write_preprocessing_artifacts(complete)
    assert not (training_only / "holdout_records.csv").exists()
    manifest = json.loads((training_only / "source_manifest.json").read_text())
    assert manifest["output_sha256"]["holdout_records.csv"] == analysis.sha256(
        complete / "holdout_records.csv"
    )


def test_empty_training_reports_skipped_streams_reproducibly(tmp_path):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    output = tmp_path / "exploration"
    write_preprocessing_artifacts(preprocessing, include_holdout=False)
    training = preprocessing / "training_records.csv"
    write_csv(training, [])
    manifest_path = preprocessing / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_sha256"][training.name] = analysis.sha256(training)
    manifest_path.write_text(json.dumps(manifest))
    write_grid(grid)
    analysis.run_exploration(preprocessing, grid, output)
    hashes = {path.name: analysis.sha256(path) for path in output.iterdir()}
    analysis.run_exploration(preprocessing, grid, output)
    assert hashes == {path.name: analysis.sha256(path) for path in output.iterdir()}
    with (output / "threshold_exploration.csv").open() as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == len(PatternStream)
    assert all(row["skipped_reason"] == "no_eligible_history" for row in rows)
    assert all(row["retained_rule_count"] == "0" and row["mean_lift"] == "" for row in rows)


@pytest.mark.parametrize("nested", [False, True])
def test_evaluation_does_not_delete_a_frozen_lock_in_its_output(tmp_path, monkeypatch, nested):
    preprocessing = tmp_path / "preprocessing"
    grid = tmp_path / "grid.json"
    selected = tmp_path / "selected.json"
    exploration = tmp_path / "exploration"
    output = tmp_path / "evaluation"
    lock = output / "evaluation_manifest.json"
    if nested:
        lock = output / "nested" / "lock.json"
    write_preprocessing_artifacts(preprocessing)
    write_grid(grid)
    write_selected(selected)
    run_training_phases(preprocessing, grid, selected, exploration, lock)
    original = lock.read_bytes()
    inode = lock.stat().st_ino
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must guard before reading"))
        with pytest.raises(ValueError, match="overlaps input"):
            analysis.run_evaluation(preprocessing, exploration, selected, lock, output)
    assert lock.read_bytes() == original
    assert lock.stat().st_ino == inode
    analysis.verify_frozen_inputs(preprocessing, exploration, selected, lock)


@pytest.mark.parametrize("phase", ["explore", "evaluate", "freeze"])
@pytest.mark.parametrize("alias", ["same", "ancestor", "nested", "resolved"])
def test_output_input_overlap_is_rejected_before_reads(tmp_path, monkeypatch, phase, alias):
    source = tmp_path / "input"
    source.mkdir()
    if alias == "same":
        output = source
    elif alias == "ancestor":
        output = tmp_path
    elif alias == "nested":
        output = source / "nested"
    else:
        link = tmp_path / "link"
        link.symlink_to(source, target_is_directory=True)
        output = link / "nested"

    def unexpected_read(*args, **kwargs):
        pytest.fail("output validation must precede input reads")

    monkeypatch.setattr(Path, "open", unexpected_read)
    with pytest.raises(ValueError, match="overlaps input"):
        if phase == "explore":
            analysis.run_exploration(source, tmp_path / "grid.json", output)
        elif phase == "evaluate":
            analysis.run_evaluation(source, tmp_path / "exploration", tmp_path / "selected.json", tmp_path / "lock.json", output)
        else:
            analysis.freeze_selection(source, tmp_path / "selected.json", output)
    assert not (source / "nested").exists()


@pytest.mark.parametrize("filename", ["exploration_manifest.json", "threshold_exploration.csv"])
def test_freeze_rejects_resolved_artifact_aliases_before_reads(tmp_path, monkeypatch, filename):
    exploration = tmp_path / "exploration"
    exploration.mkdir()
    lock = tmp_path / "lock.json"
    (exploration / filename).symlink_to(lock)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must guard before reading"))
    with pytest.raises(ValueError, match="overlaps input"):
        analysis.freeze_selection(exploration, tmp_path / "selected.json", lock)
    assert not lock.exists()


@pytest.mark.parametrize("input_name", ["selected", "lock", "training", "manifest", "exploration_csv"])
@pytest.mark.parametrize("nested", [False, True])
def test_evaluation_protects_input_aliases_and_nested_locks(tmp_path, monkeypatch, input_name, nested):
    output = tmp_path / "output"
    output.mkdir()
    protected = output / "evaluation_manifest.json"
    if nested:
        protected = protected / "nested" / "lock.json"
    preprocessing = tmp_path / "preprocessing"
    exploration = tmp_path / "exploration"
    selected = tmp_path / "selected.json"
    lock = tmp_path / "lock.json"
    if input_name == "selected":
        selected = protected
    elif input_name == "lock":
        lock = protected
    else:
        directory = exploration if input_name == "exploration_csv" else preprocessing
        directory.mkdir()
        filename = {"training": "training_records.csv", "manifest": "source_manifest.json", "exploration_csv": "threshold_exploration.csv"}[input_name]
        (directory / filename).symlink_to(protected)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read inputs"))
    with pytest.raises(ValueError, match="overlaps input"):
        analysis.run_evaluation(preprocessing, exploration, selected, lock, output)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("kind", ["root_symlink", "directory", "entry_symlink", "fifo"])
def test_non_regular_outputs_are_rejected_without_publication(tmp_path, monkeypatch, kind):
    output = tmp_path / "output"
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "unrelated.txt"
    marker.write_text("preserve")
    if kind == "root_symlink":
        output.symlink_to(target, target_is_directory=True)
    else:
        output.mkdir()
        entry = output / "holdout_evaluation.csv"
        if kind == "directory":
            entry.mkdir()
            (entry / "unrelated.txt").write_text("preserve")
        elif kind == "entry_symlink":
            entry.symlink_to(marker)
        else:
            os.mkfifo(entry)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "holdout_evaluation.csv").write_text("new")
    with pytest.raises(ValueError, match="symlink|regular file"):
        analysis._replace_output_directory(staging, output, {"holdout_evaluation.csv"})
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read inputs"))
    with pytest.raises(ValueError, match="symlink|regular file"):
        analysis.run_evaluation(tmp_path / "pre", tmp_path / "exp", tmp_path / "selected", tmp_path / "lock", output)
    monkeypatch.undo()
    assert marker.read_text() == "preserve"
    if kind == "directory":
        assert (output / "holdout_evaluation.csv" / "unrelated.txt").read_text() == "preserve"
    assert (staging / "holdout_evaluation.csv").read_text() == "new"
    assert not list(tmp_path.glob(".output-previous-*"))
