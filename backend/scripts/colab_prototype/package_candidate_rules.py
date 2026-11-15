"""CHR-100 / CHR-COLAB-004 package frozen Colab rules for offline import.

This command reads only local files. It verifies the CHR-99 provenance chain,
resolves research faculty keys through a reviewed crosswalk, and writes one
self-contained JSON artifact for the local PostgreSQL importer.

CHR-133 / CHR-COLAB-007 adds an optional reviewed reference-alias file. It maps
historical subject or room codes to their reviewed catalog codes, or excludes
the rules that reference a code the reviewer kept out of the catalog. Rule
statistics are never changed, and the alias file hash is recorded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream
from app.services.arm.rule_package import (
    CandidateRulePackage,
    RulePackageItem,
    RulePackageMetadata,
    RulePackageRule,
    RulePackageTerm,
    RulePackageThreshold,
    validate_rule_against_threshold,
    write_rule_package,
)


TRACEABILITY = "CHR-100 / CHR-COLAB-004"
_RULE_FIELDS = {
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
}
_CROSSWALK_FIELDS = {
    "research_faculty_key",
    "faculty_name",
    "faculty_id",
}
_ALIAS_FIELDS = {"kind", "historical_code", "decision", "canonical_code"}
_ALIAS_KINDS = {"subject_code", "room_code"}
_THRESHOLD_FIELDS = {
    "min_support",
    "min_confidence",
    "min_lift",
    "min_term_recurrence",
}
_CONSEQUENT_FIELDS = {"subject_code", "room_code", "meeting_days", "time_slot"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVALUATION_INPUT_HASHES = {
    "training_records.csv",
    "holdout_records.csv",
    "exploration_manifest.json",
    "threshold_exploration.csv",
    "selected_thresholds",
    "selection_lock.json",
    "analysis_script",
    "arm_mining",
    "arm_preprocessing",
}
_EVALUATION_OUTPUTS = {
    "selected_rules.csv",
    "holdout_evaluation.csv",
    "holdout_pattern_attainment.svg",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _hash_mapping(value: object, artifact: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{artifact} has no hash mapping")
    return value


def _require_hash(
    hashes: Mapping[str, object], filename: str, path: Path, artifact: str
) -> None:
    if hashes.get(filename) != sha256(path):
        raise ValueError(f"{filename} does not match {artifact}")


def _require_hash_commitments(
    hashes: Mapping[str, object], names: set[str], artifact: str
) -> None:
    missing = names - set(hashes)
    if missing:
        raise ValueError(f"{artifact} is missing hash commitments: {sorted(missing)}")
    invalid = [
        name
        for name in sorted(names)
        if not isinstance(hashes[name], str)
        or _SHA256.fullmatch(hashes[name]) is None
    ]
    if invalid:
        raise ValueError(f"{artifact} has invalid SHA-256 commitments: {invalid}")


def _verify_provenance(
    preprocessing_dir: Path,
    evaluation_dir: Path,
    source_manifest: Mapping[str, object],
    evaluation_manifest: Mapping[str, object],
) -> None:
    if evaluation_manifest.get("traceability") != "CHR-99 / CHR-COLAB-003":
        raise ValueError("evaluation manifest has invalid CHR-99 traceability")

    source_hashes = _hash_mapping(
        source_manifest.get("output_sha256"), "source_manifest.json"
    )
    _require_hash_commitments(
        source_hashes,
        {"training_records.csv", "holdout_records.csv"},
        "source_manifest.json",
    )
    evaluation_inputs = _hash_mapping(
        evaluation_manifest.get("input_sha256"), "evaluation_manifest.json"
    )
    _require_hash_commitments(
        evaluation_inputs,
        _EVALUATION_INPUT_HASHES,
        "evaluation_manifest.json",
    )
    evaluation_outputs = _hash_mapping(
        evaluation_manifest.get("output_sha256"), "evaluation_manifest.json"
    )
    _require_hash_commitments(
        evaluation_outputs,
        _EVALUATION_OUTPUTS,
        "evaluation_manifest.json",
    )

    for filename in ("training_records.csv", "holdout_records.csv"):
        path = preprocessing_dir / filename
        _require_hash(source_hashes, filename, path, "source_manifest.json")
        _require_hash(
            evaluation_inputs, filename, path, "evaluation_manifest.json"
        )
    for filename in _EVALUATION_OUTPUTS:
        _require_hash(
            evaluation_outputs,
            filename,
            evaluation_dir / filename,
            "evaluation_manifest.json",
        )


def _exact_columns(reader: csv.DictReader, fields: set[str]) -> bool:
    """True when the header names exactly ``fields``, each once."""
    names = reader.fieldnames or ()
    return len(names) == len(fields) and set(names) == fields


def _require_full_row(row: Mapping[object, object], path: Path, line: int) -> None:
    # DictReader fills missing cells with None and keys surplus cells under None.
    if None in row or None in row.values():
        raise ValueError(f"{path}:{line}: row does not match the header width")


def _normalized_name(value: str) -> str:
    return " ".join(value.split()).upper()


def load_faculty_crosswalk(path: Path) -> dict[UUID, tuple[str, UUID]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not _exact_columns(reader, _CROSSWALK_FIELDS):
            raise ValueError(
                "faculty crosswalk must have exactly research_faculty_key, "
                "faculty_name,faculty_id columns"
            )
        crosswalk: dict[UUID, tuple[str, UUID]] = {}
        for line, row in enumerate(reader, 2):
            _require_full_row(row, path, line)
            try:
                research_key = UUID(row["research_faculty_key"].strip())
                faculty_name = _normalized_name(row["faculty_name"])
                faculty_id = UUID(row["faculty_id"].strip())
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line}: invalid faculty crosswalk row") from error
            if not faculty_name:
                raise ValueError(f"{path}:{line}: faculty_name must not be blank")
            if research_key in crosswalk:
                raise ValueError(f"{path}:{line}: duplicate research_faculty_key")
            crosswalk[research_key] = faculty_name, faculty_id
    if not crosswalk:
        raise ValueError("faculty crosswalk must contain at least one reviewed mapping")
    return crosswalk


def load_reference_aliases(path: Path) -> dict[tuple[str, str], str | None]:
    """Return (kind, historical code) -> reviewed code, or None to exclude its rules."""
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not _exact_columns(reader, _ALIAS_FIELDS):
            raise ValueError(
                "reference aliases must have exactly kind,historical_code,"
                "decision,canonical_code columns"
            )
        aliases: dict[tuple[str, str], str | None] = {}
        for line, row in enumerate(reader, 2):
            _require_full_row(row, path, line)
            kind = row["kind"].strip()
            historical = row["historical_code"].strip()
            decision = row["decision"].strip()
            canonical = row["canonical_code"].strip()
            if kind not in _ALIAS_KINDS or not historical:
                raise ValueError(f"{path}:{line}: invalid reference alias kind or code")
            if decision == "alias" and canonical and canonical != historical:
                target: str | None = canonical
            elif decision == "exclude_rules" and not canonical:
                target = None
            else:
                raise ValueError(
                    f"{path}:{line}: decision must be alias with a different "
                    "canonical_code, or exclude_rules with a blank canonical_code"
                )
            if (kind, historical) in aliases:
                raise ValueError(f"{path}:{line}: duplicate reference alias")
            aliases[kind, historical] = target
    if not aliases:
        raise ValueError("reference aliases must contain at least one reviewed row")
    chained = sorted(
        (kind, target)
        for (kind, _), target in aliases.items()
        if target is not None and (kind, target) in aliases
    )
    if chained:
        # One pass only: an alias target must not itself be aliased or excluded.
        raise ValueError(f"reference alias targets must not be alias keys: {chained}")
    return aliases


def _training_terms(
    training_path: Path,
    source_manifest: Mapping[str, object],
    evaluation_manifest: Mapping[str, object],
) -> tuple[RulePackageTerm, ...]:
    source_hashes = _hash_mapping(
        source_manifest.get("output_sha256"), "source_manifest.json"
    )
    evaluation_hashes = _hash_mapping(
        evaluation_manifest.get("input_sha256"), "evaluation_manifest.json"
    )
    _require_hash(
        source_hashes,
        "training_records.csv",
        training_path,
        "source_manifest.json",
    )
    _require_hash(
        evaluation_hashes,
        "training_records.csv",
        training_path,
        "evaluation_manifest.json",
    )

    with training_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"academic_year", "term_number"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("training_records.csv is missing academic_year or term_number")
        periods = set()
        for line, row in enumerate(reader, 2):
            try:
                periods.add(
                    (
                        row["academic_year"].strip(),
                        _exact_integer(row["term_number"], "term_number"),
                    )
                )
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(
                    f"{training_path}:{line}: invalid training term"
                ) from error
    policy = HistoricalInclusionPolicy()
    return tuple(
        RulePackageTerm(academic_year=year, term_number=term)
        for year, term in sorted(periods)
        if any(policy.is_eligible(stream, year) for stream in PatternStream)
    )


def _exact_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field} must be an exact integer")
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()) is None:
        raise ValueError(f"{field} must be an exact integer")
    return int(value)


def _thresholds(
    evaluation_manifest: Mapping[str, object],
) -> tuple[RulePackageThreshold, ...]:
    raw = evaluation_manifest.get("selected_thresholds")
    expected_streams = {stream.value for stream in PatternStream}
    if not isinstance(raw, Mapping) or set(raw) != expected_streams:
        raise ValueError("evaluation manifest must contain all four selected thresholds")
    term_counts = evaluation_manifest.get("mined_training_term_counts")
    if (
        not isinstance(term_counts, Mapping)
        or set(term_counts) != expected_streams
        or any(type(value) is not int or value < 0 for value in term_counts.values())
    ):
        raise ValueError(
            "evaluation manifest mined_training_term_counts must be non-negative "
            "integers for exactly four streams"
        )
    policy = HistoricalInclusionPolicy()
    thresholds = []
    for stream in PatternStream:
        values = raw[stream.value]
        if not isinstance(values, Mapping) or set(values) != _THRESHOLD_FIELDS:
            raise ValueError(f"invalid selected threshold fields for {stream.value}")
        thresholds.append(
            RulePackageThreshold(
                pattern_type=stream,
                min_support=values["min_support"],
                min_confidence=values["min_confidence"],
                min_lift=values["min_lift"],
                min_term_recurrence=_exact_integer(
                    values["min_term_recurrence"], "min_term_recurrence"
                ),
                training_term_count=term_counts[stream.value],
                earliest_academic_year=policy.earliest_academic_year(stream),
            )
        )
    return tuple(thresholds)


def _rule_items(
    stream: PatternStream, faculty_id: UUID, values: Mapping[str, str]
) -> tuple[tuple[RulePackageItem, ...], tuple[RulePackageItem, ...]]:
    populated = {field for field in _CONSEQUENT_FIELDS if values[field]}
    expected = {
        PatternStream.TEACHER_SUBJECT: {"subject_code"},
        PatternStream.TEACHER_DAY: {"meeting_days"},
        PatternStream.TEACHER_TIME: {"time_slot"},
        PatternStream.TEACHER_SUBJECT_ROOM: {"subject_code", "room_code"},
    }[stream]
    if populated != expected:
        raise ValueError(
            f"{stream.value} must populate exactly these rule fields: {sorted(expected)}"
        )

    faculty = RulePackageItem(kind="faculty_id", value=str(faculty_id))
    if stream is PatternStream.TEACHER_SUBJECT:
        return (faculty,), (
            RulePackageItem(kind="subject_code", value=values["subject_code"]),
        )
    if stream is PatternStream.TEACHER_DAY:
        return (faculty,), (
            RulePackageItem(kind="meeting_days", value=values["meeting_days"]),
        )
    if stream is PatternStream.TEACHER_TIME:
        return (faculty,), (
            RulePackageItem(kind="time_slot", value=values["time_slot"]),
        )
    return (
        faculty,
        RulePackageItem(kind="subject_code", value=values["subject_code"]),
    ), (RulePackageItem(kind="room_code", value=values["room_code"]),)


def load_rules(
    path: Path,
    crosswalk: Mapping[UUID, tuple[str, UUID]],
    aliases: Mapping[tuple[str, str], str | None] | None = None,
    thresholds: Sequence[RulePackageThreshold] = (),
) -> tuple[tuple[RulePackageRule, ...], dict[str, int]]:
    """Return packaged rules and, per stream, how many reviewed exclusions dropped.

    An excluded rule must pass the same threshold checks as a retained one, so
    an exclusion can never hide a rule the package itself would refuse.
    """
    aliases = aliases or {}
    if aliases and not thresholds:
        raise ValueError("reference aliases require the selected thresholds")
    threshold_by_stream = {threshold.pattern_type: threshold for threshold in thresholds}
    rules: list[RulePackageRule] = []
    excluded = {stream.value: 0 for stream in PatternStream}
    used: set[tuple[str, str]] = set()
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not _exact_columns(reader, _RULE_FIELDS):
            raise ValueError("selected_rules.csv does not have the CHR-99 rule columns")
        for line, row in enumerate(reader, 2):
            _require_full_row(row, path, line)
            try:
                stream = PatternStream(row["stream"].strip())
                research_key = UUID(row["research_faculty_key"].strip())
                faculty_name = _normalized_name(row["faculty_name"])
                mapped_name, faculty_id = crosswalk[research_key]
                if faculty_name != mapped_name:
                    raise ValueError("faculty_name does not match the reviewed crosswalk")
                values = {field: row[field].strip() for field in _CONSEQUENT_FIELDS}
                keys = [(kind, values[kind]) for kind in sorted(_ALIAS_KINDS)]
                matched = [key for key in keys if key in aliases]
                used.update(matched)
                # Validate an excluded rule like any other before discarding it.
                exclude = any(aliases[key] is None for key in matched)
                for kind, code in matched:
                    values[kind] = aliases[kind, code] or code
                antecedent, consequent = _rule_items(stream, faculty_id, values)
                rule = RulePackageRule(
                    pattern_type=stream,
                    research_faculty_key=research_key,
                    faculty_name=faculty_name,
                    antecedent=antecedent,
                    consequent=consequent,
                    support=row["support"].strip(),
                    confidence=row["confidence"].strip(),
                    lift=row["lift"].strip(),
                    term_recurrence=_exact_integer(
                        row["term_recurrence"], "term_recurrence"
                    ),
                    guidance_score=row["guidance_score"].strip(),
                )
                if exclude:
                    validate_rule_against_threshold(rule, threshold_by_stream[stream])
            except KeyError as error:
                raise ValueError(
                    f"{path}:{line}: research faculty key has no reviewed mapping"
                ) from error
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line}: invalid selected rule: {error}") from error
            if exclude:
                excluded[stream.value] += 1
                continue
            rules.append(rule)
    unused = sorted(set(aliases) - used)
    if unused:
        raise ValueError(f"reference aliases match no selected rule: {unused}")
    return tuple(sorted(rules, key=lambda rule: rule.identity)), excluded


def build_candidate_package(
    preprocessing_dir: Path,
    evaluation_dir: Path,
    faculty_crosswalk: Path,
    *,
    reference_aliases: Path | None = None,
    packaged_at: datetime | None = None,
) -> CandidateRulePackage:
    source_manifest_path = preprocessing_dir / "source_manifest.json"
    training_path = preprocessing_dir / "training_records.csv"
    evaluation_manifest_path = evaluation_dir / "evaluation_manifest.json"
    selected_rules_path = evaluation_dir / "selected_rules.csv"

    source_manifest = _json_object(source_manifest_path)
    evaluation_manifest = _json_object(evaluation_manifest_path)
    if evaluation_manifest.get("phase") != "frozen_threshold_holdout_evaluation":
        raise ValueError("evaluation manifest is not a frozen holdout evaluation")
    if evaluation_manifest.get("source_manifest_sha256") != sha256(
        source_manifest_path
    ):
        raise ValueError("source_manifest.json does not match the evaluation manifest")
    _verify_provenance(
        preprocessing_dir,
        evaluation_dir,
        source_manifest,
        evaluation_manifest,
    )
    if "mined_at" not in evaluation_manifest:
        raise ValueError("evaluation manifest has no mined_at timestamp")

    crosswalk = load_faculty_crosswalk(faculty_crosswalk)
    aliases = load_reference_aliases(reference_aliases) if reference_aliases else None
    thresholds = _thresholds(evaluation_manifest)
    rules, excluded = load_rules(selected_rules_path, crosswalk, aliases, thresholds)
    retained_counts = evaluation_manifest.get("retained_rule_counts")
    expected_streams = {stream.value for stream in PatternStream}
    if (
        not isinstance(retained_counts, Mapping)
        or set(retained_counts) != expected_streams
        or any(type(value) is not int or value < 0 for value in retained_counts.values())
    ):
        raise ValueError(
            "evaluation manifest retained_rule_counts must be non-negative integers "
            "for exactly four streams"
        )
    actual_counts = {
        stream.value: sum(rule.pattern_type == stream for rule in rules)
        + excluded[stream.value]
        for stream in PatternStream
    }
    if retained_counts != actual_counts:
        raise ValueError("selected_rules.csv counts do not match the evaluation manifest")
    return CandidateRulePackage(
        schema_version=1,
        traceability=TRACEABILITY,
        candidate_status="draft",
        metadata=RulePackageMetadata(
            mined_at=evaluation_manifest["mined_at"],
            packaged_at=packaged_at or datetime.now(timezone.utc),
            source_manifest_sha256=sha256(source_manifest_path),
            evaluation_manifest_sha256=sha256(evaluation_manifest_path),
            selected_rules_sha256=sha256(selected_rules_path),
            faculty_crosswalk_sha256=sha256(faculty_crosswalk),
            reference_aliases_sha256=(
                sha256(reference_aliases) if reference_aliases else None
            ),
            excluded_rule_counts=excluded if aliases else None,
        ),
        training_terms=_training_terms(
            training_path, source_manifest, evaluation_manifest
        ),
        thresholds=thresholds,
        rules=rules,
    )


def package_candidate_rules(
    preprocessing_dir: Path,
    evaluation_dir: Path,
    faculty_crosswalk: Path,
    output: Path,
    reference_aliases: Path | None = None,
) -> CandidateRulePackage:
    inputs = {
        preprocessing_dir / "source_manifest.json",
        preprocessing_dir / "training_records.csv",
        evaluation_dir / "evaluation_manifest.json",
        evaluation_dir / "selected_rules.csv",
        faculty_crosswalk,
        *([reference_aliases] if reference_aliases else []),
    }
    if output in inputs or output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("output must not overwrite an input artifact")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("output must be a regular file path")

    package = build_candidate_package(
        preprocessing_dir,
        evaluation_dir,
        faculty_crosswalk,
        reference_aliases=reference_aliases,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_rule_package(package, output)
    return package


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preprocessing-dir", type=Path, required=True)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--faculty-crosswalk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-aliases", type=Path)
    args = parser.parse_args(argv)

    package = package_candidate_rules(
        args.preprocessing_dir,
        args.evaluation_dir,
        args.faculty_crosswalk,
        args.output,
        args.reference_aliases,
    )
    excluded = sum((package.metadata.excluded_rule_counts or {}).values())
    print(
        f"wrote {args.output}: {len(package.rules)} rules, "
        f"{len(package.training_terms)} training terms, "
        f"{excluded} reviewed exclusions, status=draft"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
