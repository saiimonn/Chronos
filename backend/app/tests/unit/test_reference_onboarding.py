"""CHR-133 / CHR-COLAB-007 reviewed reference onboarding contracts."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from app.cli import onboard_rule_references as onboarding_cli
from app.services.catalog.reference_onboarding import (
    APPROVAL_STATUS,
    CATALOG_APPROVAL,
    RESEARCH_APPROVAL_STATUS,
    REVIEW_STATUS,
    ReferenceOnboardingError,
    load_reviewed_reference_data,
)


FILES = (
    "faculty_onboarding.pending.csv",
    "subjects_onboarding.pending.csv",
    "rooms_onboarding.pending.csv",
    "terms_onboarding.pending.csv",
)
RESEARCH_FACULTY_ID = "10000000-0000-0000-0000-000000000001"
FACULTY_ID = "20000000-0000-0000-0000-000000000001"
BUILDING_ID = "30000000-0000-0000-0000-000000000001"
TERM_ID = "40000000-0000-0000-0000-000000000001"

HEADERS = {
    FILES[0]: (
        "research_faculty_key",
        "historical_faculty_name",
        "research_identity_status",
        "historical_record_count",
        "historical_period_count",
        "frozen_training_record_count",
        "frozen_holdout_record_count",
        "required_by_frozen_72_rules",
        "faculty_id",
        "reviewed_full_name",
        "is_full_time",
        "availability_term_id",
        "availability_time_slot_ids",
        "catalog_membership_decision",
        "review_status",
    ),
    FILES[1]: (
        "historical_subject_code",
        "historical_record_count",
        "historical_titles_json",
        "historical_units_json",
        "historical_source_codes_json",
        "exact_seed_catalog_match",
        "proposed_subject_code",
        "proposed_subject_title",
        "proposed_units",
        "proposed_subject_type",
        "catalog_membership_decision",
        "review_status",
    ),
    FILES[2]: (
        "historical_room_code",
        "historical_record_count",
        "reviewed_room_code",
        "building_id",
        "building_name",
        "building_code",
        "room_type",
        "expanded",
        "derived_capacity_after_type_review",
        "catalog_membership_decision",
        "review_status",
    ),
    FILES[3]: (
        "academic_year",
        "term_number",
        "source_sheet",
        "canonical_record_count",
        "frozen_research_partition",
        "term_id",
        "proposed_is_active",
        "finalized_history_approval",
        "review_status",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=HEADERS[path.name], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _rows(*, reviewed: bool, partition: str = "training") -> dict[str, list[dict[str, str]]]:
    undecided = "<TBD>"
    return {
        FILES[0]: [
            {
                "research_faculty_key": RESEARCH_FACULTY_ID,
                "historical_faculty_name": "<HISTORICAL FACULTY>",
                "research_identity_status": "in_frozen_identity_map",
                "historical_record_count": "10",
                "historical_period_count": "2",
                "frozen_training_record_count": "8",
                "frozen_holdout_record_count": "2",
                "required_by_frozen_72_rules": "true",
                "faculty_id": FACULTY_ID if reviewed else undecided,
                "reviewed_full_name": "<REVIEWED FACULTY>" if reviewed else undecided,
                "is_full_time": "true" if reviewed else undecided,
                "availability_term_id": "" if reviewed else undecided,
                "availability_time_slot_ids": "" if reviewed else undecided,
                "catalog_membership_decision": CATALOG_APPROVAL if reviewed else undecided,
                "review_status": REVIEW_STATUS if reviewed else "pending_administrator_review",
            }
        ],
        FILES[1]: [
            {
                "historical_subject_code": "<HIST-SUBJECT>",
                "historical_record_count": "10",
                "historical_titles_json": '["<HISTORICAL SUBJECT>"]',
                "historical_units_json": "[3]",
                "historical_source_codes_json": '["<HIST-SUBJECT>"]',
                "exact_seed_catalog_match": "false",
                "proposed_subject_code": "<SUBJECT>" if reviewed else undecided,
                "proposed_subject_title": "<REVIEWED SUBJECT>" if reviewed else undecided,
                "proposed_units": "3" if reviewed else undecided,
                "proposed_subject_type": "LEC" if reviewed else undecided,
                "catalog_membership_decision": CATALOG_APPROVAL if reviewed else undecided,
                "review_status": REVIEW_STATUS if reviewed else "pending_administrator_review",
            }
        ],
        FILES[2]: [
            {
                "historical_room_code": "<HIST-ROOM>",
                "historical_record_count": "10",
                "reviewed_room_code": "<ROOM>" if reviewed else undecided,
                "building_id": BUILDING_ID if reviewed else undecided,
                "building_name": "<BUILDING>" if reviewed else undecided,
                "building_code": "<BLDG>" if reviewed else undecided,
                "room_type": "lecture" if reviewed else undecided,
                "expanded": "true" if reviewed else undecided,
                "derived_capacity_after_type_review": "45" if reviewed else undecided,
                "catalog_membership_decision": CATALOG_APPROVAL if reviewed else undecided,
                "review_status": REVIEW_STATUS if reviewed else "pending_administrator_review",
            }
        ],
        FILES[3]: [
            {
                "academic_year": "2023-2024",
                "term_number": "1",
                "source_sheet": "<SOURCE SHEET>",
                "canonical_record_count": "10",
                "frozen_research_partition": partition,
                "term_id": TERM_ID if reviewed else undecided,
                "proposed_is_active": "false",
                "finalized_history_approval": (
                    "approved_as_inactive_training_reference" if reviewed else undecided
                ),
                "review_status": REVIEW_STATUS if reviewed else "pending_administrator_review",
            }
        ],
    }


def _approve(source: Path, reviewed: Path, approval_path: Path) -> None:
    source_manifest = source / "review_manifest.json"
    source_manifest_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    approval_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "traceability": "CHR-133 / CHR-COLAB-007",
                "approval_status": APPROVAL_STATUS,
                "reviewer": "Synthetic Department Administrator",
                "reviewed_on": "2026-10-23",
                "evidence_reference": "synthetic-review-evidence",
                "source_review_manifest_sha256": _sha256(source_manifest),
                "source_manifest_sha256": source_manifest_payload[
                    "source_manifest_sha256"
                ],
                "source_worksheet_sha256": {
                    name: _sha256(source / name) for name in FILES
                },
                "reviewed_worksheet_sha256": {
                    name: _sha256(reviewed / name) for name in FILES
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _bundle(tmp_path: Path, *, partition: str = "training") -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    reviewed = tmp_path / "reviewed"
    source.mkdir()
    reviewed.mkdir()
    for name, rows in _rows(reviewed=False, partition=partition).items():
        _write_csv(source / name, rows)
    source_manifest = {
        "traceability": "CHR-133 / CHR-COLAB-007",
        "status": "scoped_onboarding_pending_administrator_review",
        "row_counts": {name: 1 for name in FILES},
        "source_manifest_sha256": "1" * 64,
        "output_sha256": {name: _sha256(source / name) for name in FILES},
        "administrator_reviewed": False,
        "database_writes_performed": False,
    }
    (source / "review_manifest.json").write_text(
        json.dumps(source_manifest, sort_keys=True), encoding="utf-8"
    )
    for name, rows in _rows(reviewed=True, partition=partition).items():
        _write_csv(reviewed / name, rows)
    approval = reviewed / "approval_manifest.json"
    _approve(source, reviewed, approval)
    return source, reviewed, approval


def _change_cell(path: Path, column: str, value: str) -> None:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    rows[0][column] = value
    _write_csv(path, rows)


@pytest.mark.parametrize("partition", ["training", "frozen_training"])
def test_loads_approved_reviewed_references_without_inventing_availability(
    tmp_path: Path, partition: str,
):
    source, reviewed, approval = _bundle(tmp_path, partition=partition)

    data = load_reviewed_reference_data(source, reviewed, approval)

    assert len(data.faculty) == len(data.subjects) == len(data.rooms) == 1
    assert len(data.buildings) == len(data.terms) == 1
    assert str(data.faculty[0].faculty_id) == FACULTY_ID
    assert data.faculty[0].full_name == "<REVIEWED FACULTY>"
    assert data.faculty[0].is_full_time is True
    assert data.rooms[0].capacity == 45
    assert data.terms[0].is_active is False
    assert data.input_sha256["approval_manifest.json"] == _sha256(approval)


def test_rejects_a_review_that_changes_historical_source_evidence(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    _change_cell(reviewed / FILES[1], "historical_record_count", "11")
    _approve(source, reviewed, approval)

    with pytest.raises(ReferenceOnboardingError, match="immutable source column"):
        load_reviewed_reference_data(source, reviewed, approval)


def test_rejects_a_reviewed_file_changed_after_approval(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    _change_cell(reviewed / FILES[1], "proposed_subject_title", "<CHANGED>")

    with pytest.raises(ReferenceOnboardingError, match="approval manifest hash"):
        load_reviewed_reference_data(source, reviewed, approval)


def test_reports_a_missing_reviewed_worksheet_as_a_contract_error(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    (reviewed / FILES[1]).unlink()

    with pytest.raises(ReferenceOnboardingError, match="reviewed worksheet is missing"):
        load_reviewed_reference_data(source, reviewed, approval)


@pytest.mark.parametrize("field", ["reviewer", "evidence_reference"])
def test_rejects_placeholder_approval_metadata(tmp_path: Path, field: str):
    source, reviewed, approval = _bundle(tmp_path)
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload[field] = f"<{field.upper()}>"
    approval.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ReferenceOnboardingError, match=field):
        load_reviewed_reference_data(source, reviewed, approval)


def test_rejects_an_invalid_upstream_source_manifest_hash(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    source_manifest_path = source / "review_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["source_manifest_sha256"] = "not-a-sha256"
    source_manifest_path.write_text(
        json.dumps(source_manifest, sort_keys=True), encoding="utf-8"
    )
    _approve(source, reviewed, approval)

    with pytest.raises(ReferenceOnboardingError, match="source_manifest_sha256"):
        load_reviewed_reference_data(source, reviewed, approval)


@pytest.mark.parametrize("partition", ["holdout", "frozen_holdout", "no_canonical_records", "<TBD>"])
def test_rejects_a_non_training_term_reference(tmp_path: Path, partition: str):
    source, reviewed, approval = _bundle(tmp_path, partition=partition)

    with pytest.raises(ReferenceOnboardingError, match="training partition"):
        load_reviewed_reference_data(source, reviewed, approval)


@pytest.mark.parametrize(
    ("filename", "column", "value", "message"),
    [
        (FILES[0], "faculty_id", RESEARCH_FACULTY_ID, "research faculty key"),
        (
            FILES[0],
            "availability_term_id",
            TERM_ID,
            "availability fields must stay blank",
        ),
        (FILES[2], "derived_capacity_after_type_review", "40", "derived capacity"),
        (FILES[3], "review_status", "pending", "review_status"),
    ],
)
def test_rejects_unapproved_or_derived_review_values(
    tmp_path: Path, filename: str, column: str, value: str, message: str
):
    source, reviewed, approval = _bundle(tmp_path)
    _change_cell(reviewed / filename, column, value)
    _approve(source, reviewed, approval)

    with pytest.raises(ReferenceOnboardingError, match=message):
        load_reviewed_reference_data(source, reviewed, approval)


@pytest.mark.parametrize(
    ("review_status", "excluded"),
    [("approved_by_department_administrator", True), ("pending", False)],
)
def test_reviewed_room_exclusion_skips_only_an_approved_exclusion(
    tmp_path: Path, review_status: str, excluded: bool
):
    source, reviewed, approval = _bundle(tmp_path)
    with (source / FILES[2]).open(newline="", encoding="utf-8") as pending:
        row = next(csv.DictReader(pending))
    row |= {
        "catalog_membership_decision": "excluded_from_physical_inventory",
        "review_status": review_status,
    }
    _write_csv(reviewed / FILES[2], [row])
    _approve(source, reviewed, approval)

    if excluded:
        data = load_reviewed_reference_data(source, reviewed, approval)
        assert data.rooms == data.buildings == ()
        assert len(data.faculty) == len(data.subjects) == len(data.terms) == 1
    else:
        with pytest.raises(ReferenceOnboardingError, match="catalog_membership_decision"):
            load_reviewed_reference_data(source, reviewed, approval)


def _research_review(
    source: Path, reviewed: Path, approval: Path, *, rows_status: str, role: str | None
) -> None:
    for name in FILES:
        _change_cell(reviewed / name, "review_status", rows_status)
    _approve(source, reviewed, approval)
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["approval_status"] = RESEARCH_APPROVAL_STATUS
    payload["reviewer"] = "Synthetic Researcher"
    if role is not None:
        payload["reviewer_role"] = role
    approval.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_loads_a_research_only_review_by_a_named_researcher(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    _research_review(
        source, reviewed, approval,
        rows_status=RESEARCH_APPROVAL_STATUS, role="Researcher",
    )

    data = load_reviewed_reference_data(source, reviewed, approval)

    assert len(data.faculty) == len(data.rooms) == len(data.terms) == 1


@pytest.mark.parametrize("role", [None, "", "<REVIEWER_ROLE>"])
def test_research_only_review_requires_the_reviewer_role(
    tmp_path: Path, role: str | None
):
    source, reviewed, approval = _bundle(tmp_path)
    _research_review(
        source, reviewed, approval, rows_status=RESEARCH_APPROVAL_STATUS, role=role
    )

    with pytest.raises(ReferenceOnboardingError, match="reviewer_role"):
        load_reviewed_reference_data(source, reviewed, approval)


def test_rejects_rows_whose_status_differs_from_the_approval_manifest(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    _research_review(
        source, reviewed, approval, rows_status=APPROVAL_STATUS, role="Researcher"
    )

    with pytest.raises(ReferenceOnboardingError, match="must match the approval"):
        load_reviewed_reference_data(source, reviewed, approval)


def test_rejects_conflicting_building_definitions_across_room_rows(tmp_path: Path):
    source, reviewed, approval = _bundle(tmp_path)
    for directory, is_reviewed in ((source, False), (reviewed, True)):
        rows = _rows(reviewed=is_reviewed)[FILES[2]]
        second = rows[0] | {
            "historical_room_code": "<HIST-ROOM-2>",
            "reviewed_room_code": "<ROOM-2>" if is_reviewed else "<TBD>",
            "building_name": "<OTHER BUILDING>" if is_reviewed else "<TBD>",
        }
        _write_csv(directory / FILES[2], rows + [second])
    source_manifest_path = source / "review_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["row_counts"][FILES[2]] = 2
    source_manifest["output_sha256"][FILES[2]] = _sha256(source / FILES[2])
    source_manifest_path.write_text(
        json.dumps(source_manifest, sort_keys=True), encoding="utf-8"
    )
    _approve(source, reviewed, approval)

    with pytest.raises(ReferenceOnboardingError, match="building_id maps to conflicting"):
        load_reviewed_reference_data(source, reviewed, approval)


def test_cli_commit_requires_confirmation_and_validation_receipt(
    tmp_path: Path, capsys
):
    source, reviewed, approval = _bundle(tmp_path)
    base = [
        "--source-dir",
        str(source),
        "--reviewed-dir",
        str(reviewed),
        "--approval-manifest",
        str(approval),
        "--commit",
    ]

    with pytest.raises(SystemExit) as missing_confirmation:
        onboarding_cli.main(base)
    assert missing_confirmation.value.code == 2
    assert "--commit requires --confirm-reviewed" in capsys.readouterr().err

    with pytest.raises(SystemExit) as missing_receipt:
        onboarding_cli.main([*base, "--confirm-reviewed"])
    assert missing_receipt.value.code == 2
    assert "--commit requires --review-manifest" in capsys.readouterr().err
