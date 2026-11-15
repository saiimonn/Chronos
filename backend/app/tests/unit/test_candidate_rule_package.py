"""CHR-100 / CHR-COLAB-004 portable candidate package tests."""

import csv
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.arm.rule_package import CandidateRulePackage, load_rule_package
from scripts.colab_prototype import inventory_candidate_references as inventory


SCRIPT = (
    Path(__file__).parents[3]
    / "scripts"
    / "colab_prototype"
    / "package_candidate_rules.py"
)
SPEC = importlib.util.spec_from_file_location("candidate_rule_packager", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
packager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packager
SPEC.loader.exec_module(packager)

RESEARCH_KEY = UUID("10000000-0000-0000-0000-000000000001")
FACULTY_ID = UUID("20000000-0000-0000-0000-000000000001")
RULE_FIELDS = (
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


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    preprocessing = tmp_path / "preprocessing"
    evaluation = tmp_path / "evaluation"
    preprocessing.mkdir()
    evaluation.mkdir()

    training = preprocessing / "training_records.csv"
    _write_csv(
        training,
        ("academic_year", "term_number"),
        [
            {"academic_year": "2017-2018", "term_number": 3},
            {"academic_year": "2023-2024", "term_number": 1},
            {"academic_year": "2023-2024", "term_number": 2},
        ],
    )
    holdout = preprocessing / "holdout_records.csv"
    holdout.write_text("synthetic holdout\n", encoding="utf-8")
    source_manifest = preprocessing / "source_manifest.json"
    source_manifest.write_text(
        json.dumps(
            {
                "output_sha256": {
                    "training_records.csv": packager.sha256(training),
                    "holdout_records.csv": packager.sha256(holdout),
                }
            }
        ),
        encoding="utf-8",
    )

    common = {
        "research_faculty_key": str(RESEARCH_KEY),
        "faculty_name": "FACULTY A",
        "support": "0.5000",
        "confidence": "0.7500",
        "lift": "1.5000",
        "term_recurrence": 2,
        "guidance_score": "0.7500",
    }
    rules = evaluation / "selected_rules.csv"
    _write_csv(
        rules,
        RULE_FIELDS,
        [
            common
            | {
                "stream": "teacher_subject",
                "subject_code": "SUBJ-1",
                "room_code": "",
                "meeting_days": "",
                "time_slot": "",
            },
            common
            | {
                "stream": "teacher_day",
                "subject_code": "",
                "room_code": "",
                "meeting_days": "MWF",
                "time_slot": "",
            },
            common
            | {
                "stream": "teacher_time",
                "subject_code": "",
                "room_code": "",
                "meeting_days": "",
                "time_slot": "08:00-09:30",
            },
            common
            | {
                "stream": "teacher_subject_room",
                "subject_code": "SUBJ-1",
                "room_code": "ROOM-1",
                "meeting_days": "",
                "time_slot": "",
            },
        ],
    )
    thresholds = {
        stream: {
            "min_support": "0.2500",
            "min_confidence": "0.5000",
            "min_lift": "1.1000",
            "min_term_recurrence": 1,
        }
        for stream in (
            "teacher_subject",
            "teacher_day",
            "teacher_time",
            "teacher_subject_room",
        )
    }
    holdout_evaluation = evaluation / "holdout_evaluation.csv"
    holdout_evaluation.write_text("synthetic evaluation\n", encoding="utf-8")
    holdout_chart = evaluation / "holdout_pattern_attainment.svg"
    holdout_chart.write_text("<svg/>\n", encoding="utf-8")
    (evaluation / "evaluation_manifest.json").write_text(
        json.dumps(
            {
                "traceability": "CHR-99 / CHR-COLAB-003",
                "phase": "frozen_threshold_holdout_evaluation",
                "mined_at": "2026-10-02T06:00:00+00:00",
                "source_manifest_sha256": packager.sha256(source_manifest),
                "input_sha256": {
                    "training_records.csv": packager.sha256(training),
                    "holdout_records.csv": packager.sha256(holdout),
                    "exploration_manifest.json": "1" * 64,
                    "threshold_exploration.csv": "2" * 64,
                    "selected_thresholds": "3" * 64,
                    "selection_lock.json": "4" * 64,
                    "analysis_script": "5" * 64,
                    "arm_mining": "6" * 64,
                    "arm_preprocessing": "7" * 64,
                },
                "selected_thresholds": thresholds,
                "mined_training_term_counts": {
                    stream: 2 for stream in thresholds
                },
                "retained_rule_counts": {
                    stream: 1 for stream in thresholds
                },
                "output_sha256": {
                    "selected_rules.csv": packager.sha256(rules),
                    "holdout_evaluation.csv": packager.sha256(holdout_evaluation),
                    "holdout_pattern_attainment.svg": packager.sha256(holdout_chart),
                },
            }
        ),
        encoding="utf-8",
    )

    crosswalk = tmp_path / "faculty_crosswalk.csv"
    _write_csv(
        crosswalk,
        ("research_faculty_key", "faculty_name", "faculty_id"),
        [
            {
                "research_faculty_key": str(RESEARCH_KEY),
                "faculty_name": "FACULTY A",
                "faculty_id": str(FACULTY_ID),
            }
        ],
    )
    return preprocessing, evaluation, crosswalk


def test_packager_writes_a_self_contained_validated_draft(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    package = packager.build_candidate_package(
        preprocessing,
        evaluation,
        crosswalk,
        packaged_at=datetime(2026, 10, 2, 7, tzinfo=timezone.utc),
    )

    assert package.schema_version == 1
    assert package.traceability == "CHR-100 / CHR-COLAB-004"
    assert package.candidate_status == "draft"
    assert [(term.academic_year, term.term_number) for term in package.training_terms] == [
        ("2023-2024", 1),
        ("2023-2024", 2),
    ]
    assert len(package.thresholds) == len(package.rules) == 4
    assert all(
        threshold.training_term_count == 2 for threshold in package.thresholds
    )
    room_rule = next(
        rule for rule in package.rules if rule.pattern_type == "teacher_subject_room"
    )
    assert [item.kind for item in room_rule.antecedent] == [
        "faculty_id",
        "subject_code",
    ]
    assert [item.kind for item in room_rule.consequent] == ["room_code"]
    assert room_rule.item_values["faculty_id"] == str(FACULTY_ID)

    output = tmp_path / "candidate_rule_set.json"
    packager.write_rule_package(package, output)
    assert load_rule_package(output) == package


def test_package_uses_stream_transaction_terms_for_guidance_denominator(
    tmp_path: Path,
):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    rules_path = evaluation / "selected_rules.csv"
    with rules_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    room_rule = next(
        row for row in rows if row["stream"] == "teacher_subject_room"
    )
    room_rule["term_recurrence"] = 1
    _write_csv(rules_path, RULE_FIELDS, rows)

    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Both terms are eligible, but only one produced room-stream transactions.
    manifest["mined_training_term_counts"]["teacher_subject_room"] = 1
    manifest["output_sha256"]["selected_rules.csv"] = packager.sha256(rules_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    package = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk
    )

    room_threshold = next(
        threshold
        for threshold in package.thresholds
        if threshold.pattern_type == "teacher_subject_room"
    )
    packaged_room_rule = next(
        rule
        for rule in package.rules
        if rule.pattern_type == "teacher_subject_room"
    )
    assert len(package.training_terms) == 2
    assert room_threshold.training_term_count == 1
    assert packaged_room_rule.term_recurrence == 1
    assert packaged_room_rule.guidance_score == packaged_room_rule.confidence


def test_package_contract_rejects_a_stream_with_the_wrong_item_sets(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    package = packager.build_candidate_package(preprocessing, evaluation, crosswalk)
    payload = package.model_dump(mode="json")
    payload["rules"][0]["consequent"][0]["kind"] = "room_code"

    with pytest.raises(ValidationError, match="consequent must be"):
        CandidateRulePackage.model_validate(payload)


def test_package_contract_rejects_conflicting_faculty_mappings(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    payload = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk
    ).model_dump(mode="json")
    payload["rules"][1]["faculty_name"] = "A DIFFERENT FACULTY"

    with pytest.raises(ValidationError, match="cannot map to multiple"):
        CandidateRulePackage.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("support", "0.1000", "below min_support"),
        ("term_recurrence", 3, "exceeds its training terms"),
        ("guidance_score", "0.7000", "guidance_score must be"),
    ],
)
def test_package_contract_rejects_inconsistent_mining_metrics(
    tmp_path: Path, field: str, value: object, message: str
):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    payload = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk
    ).model_dump(mode="json")
    payload["rules"][0][field] = value

    with pytest.raises(ValidationError, match=message):
        CandidateRulePackage.model_validate(payload)


def test_package_contract_matches_database_precision_and_uuid_text(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    payload = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk
    ).model_dump(mode="json")
    payload["rules"][0]["support"] = "0.12345"
    with pytest.raises(ValidationError, match="decimal places"):
        CandidateRulePackage.model_validate(payload)

    payload = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk
    ).model_dump(mode="json")
    faculty_id = payload["rules"][0]["antecedent"][0]["value"]
    payload["rules"][0]["antecedent"][0]["value"] = "{" + faculty_id + "}"
    with pytest.raises(ValidationError, match="canonical UUID"):
        CandidateRulePackage.model_validate(payload)


def test_packager_rejects_unreviewed_or_tampered_inputs(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    crosswalk.write_text(
        "research_faculty_key,faculty_name,faculty_id\n"
        f"{RESEARCH_KEY},SOMEONE ELSE,{FACULTY_ID}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reviewed crosswalk"):
        packager.build_candidate_package(preprocessing, evaluation, crosswalk)

    _, evaluation, crosswalk = _inputs(tmp_path / "second")
    with (evaluation / "selected_rules.csv").open("a", encoding="utf-8") as target:
        target.write("tampered\n")
    with pytest.raises(ValueError, match="does not match evaluation_manifest"):
        packager.build_candidate_package(
            tmp_path / "second" / "preprocessing", evaluation, crosswalk
        )


def test_packager_requires_the_complete_chr99_provenance_contract(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["input_sha256"]["selection_lock.json"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="missing hash commitments"):
        packager.build_candidate_package(preprocessing, evaluation, crosswalk)


def test_packager_requires_strict_integer_retained_counts(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["retained_rule_counts"]["teacher_subject"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="non-negative integers"):
        packager.build_candidate_package(preprocessing, evaluation, crosswalk)


def test_packager_requires_strict_stream_training_term_counts(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mined_training_term_counts"]["teacher_subject_room"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="mined_training_term_counts"):
        packager.build_candidate_package(preprocessing, evaluation, crosswalk)


def _inventory_inputs(tmp_path: Path) -> tuple[Path, Path]:
    preprocessing, evaluation, _crosswalk = _inputs(tmp_path)
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _write_csv(
        evaluation / "faculty_identity_map.csv",
        ("research_faculty_key", "faculty_name"),
        [
            {"research_faculty_key": str(RESEARCH_KEY), "faculty_name": "FACULTY A"},
            {
                "research_faculty_key": "10000000-0000-0000-0000-000000000002",
                "faculty_name": "UNUSED FACULTY",
            },
        ],
    )
    for name, content in (
        ("threshold_exploration.csv", "synthetic exploration\n"),
        ("selected_thresholds.json", json.dumps(manifest["selected_thresholds"])),
        ("selection_lock.json", "{}\n"),
    ):
        (evaluation / name).write_text(content, encoding="utf-8")
    (evaluation / "exploration_manifest.json").write_text(
        json.dumps({
            "traceability": "CHR-99 / CHR-COLAB-003",
            "phase": "training_only_threshold_exploration",
            "holdout_present": False,
            "holdout_read": False,
            "output_sha256": {
                name: packager.sha256(evaluation / name)
                for name in ("faculty_identity_map.csv", "threshold_exploration.csv")
            },
        }),
        encoding="utf-8",
    )
    for key, name in (
        ("exploration_manifest.json", "exploration_manifest.json"),
        ("threshold_exploration.csv", "threshold_exploration.csv"),
        ("selected_thresholds", "selected_thresholds.json"),
        ("selection_lock.json", "selection_lock.json"),
    ):
        manifest["input_sha256"][key] = packager.sha256(evaluation / name)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return preprocessing, evaluation


def test_reference_inventory_is_deterministic_and_does_not_invent_local_ids(tmp_path: Path):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    outputs = (tmp_path / "first-inventory", tmp_path / "second-inventory")
    manifests = [
        inventory.write_reference_inventory(
            preprocessing, evaluation, output, result_commit="a" * 40
        )
        for output in outputs
    ]
    assert manifests[0] == manifests[1]
    assert manifests[0]["status"] == "preparation_only"
    assert manifests[0]["source_identity_count"] == 2
    assert manifests[0]["required_reference_counts"] == {
        "faculty": 1, "subject": 1, "room": 1, "training_term": 2,
    }
    assert manifests[0]["selected_rule_count"] == 4
    assert manifests[0]["local_database_checked"] is False
    assert manifests[0]["local_references_verified"] is False
    assert manifests[0]["candidate_package_created"] is False
    assert manifests[0]["rules_imported_or_activated"] is False
    for path in outputs[0].iterdir():
        assert path.read_bytes() == (outputs[1] / path.name).read_bytes()
    with (outputs[0] / "faculty_crosswalk.pending.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert rows == [{
        "research_faculty_key": str(RESEARCH_KEY),
        "faculty_name": "FACULTY A",
        "faculty_id": "<TBD>",
    }]
    with pytest.raises(ValueError, match="invalid faculty crosswalk"):
        packager.load_faculty_crosswalk(outputs[0] / "faculty_crosswalk.pending.csv")
    with (outputs[0] / "reference_inventory.csv").open(newline="", encoding="utf-8") as source:
        references = list(csv.DictReader(source))
    assert all(row["verification_status"] == "unverified_local_reference" for row in references)
    assert [row["reference_key"] for row in references if row["reference_type"] == "training_term"] == [
        "2023-2024:1", "2023-2024:2",
    ]
    assert all(row["retained_rule_count"] == "" for row in references if row["reference_type"] == "training_term")


@pytest.mark.parametrize("filename", ["selected_rules.csv", "faculty_identity_map.csv"])
def test_reference_inventory_rejects_tampering_before_creating_outputs(tmp_path: Path, filename: str):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    with (evaluation / filename).open("a", encoding="utf-8") as destination:
        destination.write("tampered\n")
    output = tmp_path / "inventory"
    with pytest.raises(ValueError, match="match|differs"):
        inventory.write_reference_inventory(
            preprocessing, evaluation, output, result_commit="a" * 40
        )
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["faculty_name", "duplicate_rule", "counts", "training_counts"])
def test_reference_inventory_rejects_inconsistent_frozen_reports(tmp_path: Path, mutation: str):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    rules_path = evaluation / "selected_rules.csv"
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with rules_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if mutation == "faculty_name":
        rows[0]["faculty_name"] = "OTHER FACULTY"
    elif mutation == "duplicate_rule":
        rows.append(rows[0])
    elif mutation == "counts":
        manifest["retained_rule_counts"]["teacher_subject"] = True
    else:
        manifest["mined_training_term_counts"]["teacher_day"] = True
    _write_csv(rules_path, RULE_FIELDS, rows)
    manifest["output_sha256"]["selected_rules.csv"] = packager.sha256(rules_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "inventory"
    with pytest.raises(ValueError):
        inventory.write_reference_inventory(
            preprocessing, evaluation, output, result_commit="a" * 40
        )
    assert not output.exists()


def test_reference_inventory_protects_existing_and_overlapping_output_paths(tmp_path: Path):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    for output in (evaluation, evaluation / "nested", preprocessing / "nested", tmp_path):
        with pytest.raises(ValueError, match="new directory|overlap"):
            inventory.write_reference_inventory(
                preprocessing, evaluation, output, result_commit="a" * 40
            )
    symlink = tmp_path / "alias"
    symlink.symlink_to(evaluation, target_is_directory=True)
    with pytest.raises(ValueError, match="new directory"):
        inventory.write_reference_inventory(
            preprocessing, evaluation, symlink, result_commit="a" * 40
        )


def test_reference_inventory_cli_outputs_only_reference_counts(tmp_path: Path, capsys):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    assert inventory.main([
        "--preprocessing-dir", str(preprocessing),
        "--evaluation-dir", str(evaluation),
        "--output-dir", str(tmp_path / "inventory"),
        "--result-commit", "a" * 40,
    ]) == 0
    output = capsys.readouterr().out
    assert "preparation-only" in output
    assert "unverified" in output
    assert "FACULTY A" not in output


@pytest.mark.parametrize("result_commit", ["main", "A" * 40, "a" * 39])
def test_reference_inventory_requires_an_explicit_result_commit(tmp_path: Path, result_commit: str):
    preprocessing, evaluation = _inventory_inputs(tmp_path)
    output = tmp_path / "inventory"
    with pytest.raises(ValueError, match="full lowercase Git commit"):
        inventory.write_reference_inventory(
            preprocessing, evaluation, output, result_commit=result_commit
        )
    assert not output.exists()


ALIAS_FIELDS = ("kind", "historical_code", "decision", "canonical_code")


def _aliases(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "reference_aliases.csv"
    _write_csv(path, ALIAS_FIELDS, rows)
    return path


def _statistics(rules) -> list[tuple]:
    return sorted(
        (rule.pattern_type, rule.support, rule.confidence, rule.lift,
         rule.term_recurrence, rule.guidance_score)
        for rule in rules
    )


def test_reviewed_aliases_rename_codes_without_changing_rule_statistics(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    baseline = packager.build_candidate_package(preprocessing, evaluation, crosswalk)
    aliases = _aliases(tmp_path, [
        {"kind": "subject_code", "historical_code": "SUBJ-1", "decision": "alias", "canonical_code": "SUBJ-1N"},
        {"kind": "room_code", "historical_code": "ROOM-1", "decision": "alias", "canonical_code": "ROOM1"},
    ])

    package = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk, reference_aliases=aliases
    )

    assert len(package.rules) == len(baseline.rules) == 4
    room_rule = next(r for r in package.rules if r.pattern_type == "teacher_subject_room")
    assert room_rule.item_values["subject_code"] == "SUBJ-1N"
    assert room_rule.item_values["room_code"] == "ROOM1"
    assert _statistics(package.rules) == _statistics(baseline.rules)
    assert package.metadata.reference_aliases_sha256 == packager.sha256(aliases)
    assert sum(package.metadata.excluded_rule_counts.values()) == 0
    assert baseline.metadata.reference_aliases_sha256 is None


def test_reviewed_exclusion_drops_only_matching_rules_and_records_the_count(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    aliases = _aliases(tmp_path, [
        {"kind": "room_code", "historical_code": "ROOM-1", "decision": "exclude_rules", "canonical_code": ""},
    ])

    package = packager.build_candidate_package(
        preprocessing, evaluation, crosswalk, reference_aliases=aliases
    )

    assert len(package.rules) == 3
    assert all(r.pattern_type != "teacher_subject_room" for r in package.rules)
    assert package.metadata.excluded_rule_counts["teacher_subject_room"] == 1
    output = tmp_path / "candidate_rule_set.json"
    packager.write_rule_package(package, output)
    assert load_rule_package(output) == package


def test_reference_alias_targets_cannot_chain_into_another_alias_or_exclusion(tmp_path: Path):
    aliases = _aliases(tmp_path, [
        {"kind": "room_code", "historical_code": "ROOM-1", "decision": "alias", "canonical_code": "ROOM-V"},
        {"kind": "room_code", "historical_code": "ROOM-V", "decision": "exclude_rules", "canonical_code": ""},
    ])

    with pytest.raises(ValueError, match="must not be alias keys"):
        packager.load_reference_aliases(aliases)


def test_package_without_aliases_writes_no_alias_metadata_keys(tmp_path: Path):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    output = tmp_path / "candidate_rule_set.json"
    packager.write_rule_package(
        packager.build_candidate_package(preprocessing, evaluation, crosswalk), output
    )

    metadata = json.loads(output.read_text(encoding="utf-8"))["metadata"]
    assert "reference_aliases_sha256" not in metadata
    assert "excluded_rule_counts" not in metadata


@pytest.mark.parametrize(("field", "value", "message"), [
    ("support", "not-a-number", "support"),
    ("support", "0.1000", "below min_support"),
    ("term_recurrence", "3", "term_recurrence exceeds its training terms"),
    ("guidance_score", "0.7000", "guidance_score must be 0.7500"),
])
def test_excluded_rule_is_still_validated_before_it_is_dropped(
    tmp_path: Path, field: str, value: str, message: str
):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    rules_path = evaluation / "selected_rules.csv"
    with rules_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    next(row for row in rows if row["stream"] == "teacher_subject_room")[field] = value
    _write_csv(rules_path, RULE_FIELDS, rows)
    aliases = _aliases(tmp_path, [
        {"kind": "room_code", "historical_code": "ROOM-1", "decision": "exclude_rules", "canonical_code": ""},
    ])
    thresholds = packager._thresholds(
        json.loads((evaluation / "evaluation_manifest.json").read_text(encoding="utf-8"))
    )

    with pytest.raises(ValueError, match=f"(?s)invalid selected rule.*{message}"):
        packager.load_rules(rules_path, packager.load_faculty_crosswalk(crosswalk),
                            packager.load_reference_aliases(aliases), thresholds)


@pytest.mark.parametrize("content", [
    # Duplicate header: DictReader would silently keep the last decision.
    "kind,historical_code,decision,canonical_code,decision\n"
    "room_code,ROOM-1,alias,,exclude_rules\n",
    # Missing final cell.
    "kind,historical_code,decision,canonical_code\n"
    "room_code,ROOM-1,exclude_rules\n",
    # Surplus unheaded cell.
    "kind,historical_code,decision,canonical_code\n"
    "room_code,ROOM-1,exclude_rules,,extra\n",
])
def test_reference_aliases_reject_malformed_csv(tmp_path: Path, content: str):
    path = tmp_path / "reference_aliases.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="exactly kind|header width"):
        packager.load_reference_aliases(path)


@pytest.mark.parametrize(("row", "message"), [
    ({"kind": "room_code", "historical_code": "NO-SUCH-ROOM", "decision": "alias", "canonical_code": "X"}, "match no selected rule"),
    ({"kind": "room_code", "historical_code": "ROOM-1", "decision": "alias", "canonical_code": ""}, "decision must be"),
    ({"kind": "room_code", "historical_code": "ROOM-1", "decision": "exclude_rules", "canonical_code": "X"}, "decision must be"),
    ({"kind": "faculty_id", "historical_code": "ROOM-1", "decision": "alias", "canonical_code": "X"}, "invalid reference alias"),
])
def test_reference_aliases_reject_unreviewed_or_unused_rows(tmp_path: Path, row: dict, message: str):
    preprocessing, evaluation, crosswalk = _inputs(tmp_path)
    aliases = _aliases(tmp_path, [row])

    with pytest.raises(ValueError, match=message):
        packager.build_candidate_package(
            preprocessing, evaluation, crosswalk, reference_aliases=aliases
        )
