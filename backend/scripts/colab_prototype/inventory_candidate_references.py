"""CHR-100 / CHR-COLAB-004 inventory frozen rule references without database access.

Generate a pending faculty crosswalk and an unverified local-reference checklist.
This does not package, import, activate, mine, or evaluate rules.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
import csv
import inspect
import json
from pathlib import Path
import re
from uuid import UUID

from app.services.arm.preprocessing import PatternStream
from scripts.colab_prototype.package_candidate_rules import (
    _RULE_FIELDS,
    _json_object,
    _normalized_name,
    _thresholds,
    _training_terms,
    _verify_provenance,
    sha256,
)


TRACEABILITY = "CHR-100 / CHR-COLAB-004"
_REFERENCE_FIELDS = (
    "reference_type",
    "reference_key",
    "source_label",
    "retained_rule_count",
    "expected_local_lookup",
    "verification_status",
)


def _csv_rows(path: Path, fields: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if (
            set(reader.fieldnames or ()) != fields
            or len(reader.fieldnames or ()) != len(fields)
        ):
            raise ValueError(f"{path.name} has invalid columns")
        rows = list(reader)
    if any(
        set(row) != fields or any(value is None for value in row.values())
        for row in rows
    ):
        raise ValueError(f"{path.name} has malformed rows")
    return rows


def write_reference_inventory(
    preprocessing_dir: Path,
    evaluation_dir: Path,
    output_dir: Path,
    *,
    result_commit: str,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{40}", result_commit) is None:
        raise ValueError("result_commit must be a full lowercase Git commit")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("inventory output must be a new directory")
    for source_dir in (preprocessing_dir, evaluation_dir):
        destination, source = output_dir.resolve(), source_dir.resolve()
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError("inventory output must not overlap an input directory")

    source_path = preprocessing_dir / "source_manifest.json"
    evaluation_path = evaluation_dir / "evaluation_manifest.json"
    exploration_path = evaluation_dir / "exploration_manifest.json"
    source_manifest = _json_object(source_path)
    evaluation = _json_object(evaluation_path)
    exploration = _json_object(exploration_path)
    if evaluation.get("phase") != "frozen_threshold_holdout_evaluation":
        raise ValueError("inventory requires a completed frozen holdout evaluation")
    if (
        exploration.get("traceability") != "CHR-99 / CHR-COLAB-003"
        or exploration.get("phase") != "training_only_threshold_exploration"
        or exploration.get("holdout_present") is not False
        or exploration.get("holdout_read") is not False
    ):
        raise ValueError("inventory requires a training-only exploration manifest")
    if evaluation.get("source_manifest_sha256") != sha256(source_path):
        raise ValueError("source manifest does not match the frozen evaluation")
    _verify_provenance(preprocessing_dir, evaluation_dir, source_manifest, evaluation)
    thresholds = _thresholds(evaluation)

    bound_inputs = {
        "exploration_manifest.json": exploration_path,
        "threshold_exploration.csv": evaluation_dir / "threshold_exploration.csv",
        "selected_thresholds": evaluation_dir / "selected_thresholds.json",
        "selection_lock.json": evaluation_dir / "selection_lock.json",
    }
    for name, path in bound_inputs.items():
        if evaluation["input_sha256"].get(name) != sha256(path):
            raise ValueError(f"{name} differs from the frozen evaluation")
    for manifest in (exploration, evaluation):
        outputs = manifest.get("output_sha256")
        if not isinstance(outputs, dict):
            raise ValueError("source report has no output hash mapping")
        for name, expected_hash in outputs.items():
            if not isinstance(name, str) or Path(name).name != name or "\\" in name:
                raise ValueError("report output names must be flat filenames")
            if sha256(evaluation_dir / name) != expected_hash:
                raise ValueError(f"{name} differs from its report manifest")
    if "faculty_identity_map.csv" not in exploration["output_sha256"]:
        raise ValueError("identity map has no exploration hash commitment")

    identity_path = evaluation_dir / "faculty_identity_map.csv"
    identities: dict[str, str] = {}
    for row in _csv_rows(identity_path, {"research_faculty_key", "faculty_name"}):
        key = str(UUID(row["research_faculty_key"].strip()))
        name = _normalized_name(row["faculty_name"])
        if key in identities or not name:
            raise ValueError("identity map has duplicate keys or blank faculty names")
        identities[key] = name

    rules_path = evaluation_dir / "selected_rules.csv"
    rules = _csv_rows(rules_path, _RULE_FIELDS)
    faculty_counts: Counter[str] = Counter()
    subject_counts: Counter[str] = Counter()
    room_counts: Counter[str] = Counter()
    stream_counts: Counter[str] = Counter()
    rule_identities: set[tuple[str, ...]] = set()
    for row in rules:
        stream = PatternStream(row["stream"].strip())
        key = str(UUID(row["research_faculty_key"].strip()))
        if identities.get(key) != _normalized_name(row["faculty_name"]):
            raise ValueError("selected rule faculty does not match the identity map")
        values = {
            field: row[field].strip()
            for field in ("subject_code", "room_code", "meeting_days", "time_slot")
        }
        expected = {
            PatternStream.TEACHER_SUBJECT: {"subject_code"},
            PatternStream.TEACHER_DAY: {"meeting_days"},
            PatternStream.TEACHER_TIME: {"time_slot"},
            PatternStream.TEACHER_SUBJECT_ROOM: {"subject_code", "room_code"},
        }[stream]
        if {field for field, value in values.items() if value} != expected:
            raise ValueError("selected rule has an invalid stream shape")
        identity = (stream.value, key, *(values[field] for field in sorted(values)))
        if identity in rule_identities:
            raise ValueError("selected rules contain duplicate identities")
        rule_identities.add(identity)
        stream_counts[stream.value] += 1
        faculty_counts[key] += 1
        if values["subject_code"]:
            subject_counts[values["subject_code"]] += 1
        if values["room_code"]:
            room_counts[values["room_code"]] += 1
    actual_counts = {stream.value: stream_counts[stream.value] for stream in PatternStream}
    declared_counts = evaluation.get("retained_rule_counts")
    if (
        not rules
        or not isinstance(declared_counts, dict)
        or any(type(value) is not int or value < 0 for value in declared_counts.values())
        or declared_counts != actual_counts
    ):
        raise ValueError("selected rule counts do not match the frozen evaluation")

    training_path = preprocessing_dir / "training_records.csv"
    terms = _training_terms(training_path, source_manifest, evaluation)
    if not terms:
        raise ValueError("inventory requires at least one eligible training term")
    references = []
    for kind, counts in (
        ("faculty", faculty_counts),
        ("subject", subject_counts),
        ("room", room_counts),
    ):
        for key, count in sorted(counts.items()):
            references.append({
                "reference_type": kind,
                "reference_key": key,
                "source_label": identities[key] if kind == "faculty" else key,
                "retained_rule_count": count,
                "expected_local_lookup": (
                    "<TBD>"
                    if kind == "faculty"
                    else f"{'Subject.subject_code' if kind == 'subject' else 'Room.room_code'}={key}"
                ),
                "verification_status": "unverified_local_reference",
            })
    for term in terms:
        references.append({
            "reference_type": "training_term",
            "reference_key": f"{term.academic_year}:{term.term_number}",
            "source_label": f"{term.academic_year} Term {term.term_number}",
            "retained_rule_count": "",
            "expected_local_lookup": (
                f"Term.academic_year={term.academic_year};"
                f"Term.term_number={term.term_number}"
            ),
            "verification_status": "unverified_local_reference",
        })

    crosswalk = [
        {
            "research_faculty_key": key,
            "faculty_name": identities[key],
            "faculty_id": "<TBD>",
        }
        for key in sorted(faculty_counts)
    ]
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, fields, rows in (
        (
            "faculty_crosswalk.pending.csv",
            ("research_faculty_key", "faculty_name", "faculty_id"),
            crosswalk,
        ),
        ("reference_inventory.csv", _REFERENCE_FIELDS, references),
    ):
        with (output_dir / name).open("x", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    manifest = {
        "schema_version": 1,
        "traceability": TRACEABILITY,
        "status": "preparation_only",
        "source_result_commit": result_commit,
        "source_evaluation_traceability": evaluation["traceability"],
        "local_database_checked": False,
        "local_references_verified": False,
        "candidate_package_created": False,
        "rules_imported_or_activated": False,
        "selected_rule_count": len(rules),
        "retained_rule_counts": actual_counts,
        "source_identity_count": len(identities),
        "required_reference_counts": {
            "faculty": len(faculty_counts),
            "subject": len(subject_counts),
            "room": len(room_counts),
            "training_term": len(terms),
        },
        "mined_training_term_counts": {
            threshold.pattern_type.value: threshold.training_term_count
            for threshold in thresholds
        },
        "input_sha256": {
            "source_manifest.json": sha256(source_path),
            "evaluation_manifest.json": sha256(evaluation_path),
            "faculty_identity_map.csv": sha256(identity_path),
            "selected_rules.csv": sha256(rules_path),
            "training_records.csv": sha256(training_path),
            "holdout_records.csv": sha256(preprocessing_dir / "holdout_records.csv"),
            **{name: sha256(path) for name, path in bound_inputs.items()},
        },
        "generator_sha256": sha256(Path(__file__)),
        "packager_sha256": sha256(
            Path(__file__).with_name("package_candidate_rules.py")
        ),
        "arm_preprocessing_sha256": sha256(Path(inspect.getfile(PatternStream))),
        "output_sha256": {
            path.name: sha256(path) for path in sorted(output_dir.iterdir())
        },
    }
    (output_dir / "inventory_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preprocessing-dir", type=Path, required=True)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result-commit", required=True)
    args = parser.parse_args(argv)
    manifest = write_reference_inventory(
        args.preprocessing_dir,
        args.evaluation_dir,
        args.output_dir,
        result_commit=args.result_commit,
    )
    print(f"wrote preparation-only inventory: {manifest['required_reference_counts']}")
    print(
        "Local references are unverified; no package, database query, "
        "import, or activation occurred."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
