"""CHR-133 / CHR-COLAB-007 reviewed reference onboarding."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.domain import RoomType, SubjectType, academic_year_start, derive_room_capacity
from app.domain.calendar import generate_windows
from app.models import Building, Faculty, Room, Subject, Term, TimeSlot
from app.services.catalog.time_slots import ensure_canonical_slots


TRACEABILITY = "CHR-133 / CHR-COLAB-007"
APPROVAL_STATUS = "approved_by_department_administrator"
CATALOG_APPROVAL = "approved_for_local_catalog"
ROOM_EXCLUSION = "excluded_from_physical_inventory"
REVIEW_STATUS = "approved_by_department_administrator"
# Thesis research only: a named researcher may review references for a draft
# RuleSet. It never substitutes for administrator approval of activation.
RESEARCH_APPROVAL_STATUS = "approved_for_research_by_researcher"
APPROVAL_STATUSES = (APPROVAL_STATUS, RESEARCH_APPROVAL_STATUS)
HISTORY_APPROVAL = "approved_as_inactive_training_reference"
SOURCE_MANIFEST = "review_manifest.json"
WORKSHEETS = (
    "faculty_onboarding.pending.csv",
    "subjects_onboarding.pending.csv",
    "rooms_onboarding.pending.csv",
    "terms_onboarding.pending.csv",
)

HEADERS = {
    WORKSHEETS[0]: (
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
    WORKSHEETS[1]: (
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
    WORKSHEETS[2]: (
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
    WORKSHEETS[3]: (
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

IMMUTABLE_COLUMNS = {
    WORKSHEETS[0]: HEADERS[WORKSHEETS[0]][:8],
    WORKSHEETS[1]: HEADERS[WORKSHEETS[1]][:6],
    WORKSHEETS[2]: HEADERS[WORKSHEETS[2]][:2],
    WORKSHEETS[3]: HEADERS[WORKSHEETS[3]][:5],
}

KEY_COLUMNS = {
    WORKSHEETS[0]: ("research_faculty_key",),
    WORKSHEETS[1]: ("historical_subject_code",),
    WORKSHEETS[2]: ("historical_room_code",),
    WORKSHEETS[3]: ("academic_year", "term_number"),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReferenceOnboardingError(ValueError):
    """The reviewed files cannot safely drive local catalog writes."""

    def __init__(self, violations: list[str] | str) -> None:
        self.violations = (
            [violations] if isinstance(violations, str) else list(violations)
        )
        super().__init__("; ".join(self.violations))


@dataclass(frozen=True, slots=True)
class FacultyReference:
    faculty_id: UUID
    full_name: str
    is_full_time: bool


@dataclass(frozen=True, slots=True)
class SubjectReference:
    subject_code: str
    subject_title: str
    units: int
    subject_type: SubjectType


@dataclass(frozen=True, slots=True)
class BuildingReference:
    building_id: UUID
    building_name: str
    building_code: str


@dataclass(frozen=True, slots=True)
class RoomReference:
    room_code: str
    building_id: UUID
    building_name: str
    capacity: int
    room_type: RoomType
    expanded: bool


@dataclass(frozen=True, slots=True)
class TermReference:
    term_id: UUID
    academic_year: str
    term_number: int
    is_active: bool = False


@dataclass(frozen=True, slots=True)
class ReviewedReferenceData:
    faculty: tuple[FacultyReference, ...]
    subjects: tuple[SubjectReference, ...]
    buildings: tuple[BuildingReference, ...]
    rooms: tuple[RoomReference, ...]
    terms: tuple[TermReference, ...]
    row_counts: dict[str, int]
    input_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReferenceOnboardingReport:
    created: dict[str, int]
    reused: dict[str, int]


@dataclass(frozen=True, slots=True)
class ReferenceDatabaseState:
    database: dict[str, str]
    alembic_revision: str
    catalog_counts: dict[str, int]
    catalog_sha256: str


@dataclass(frozen=True, slots=True)
class _Row:
    line: int
    values: dict[str, str]

    def get(self, column: str) -> str:
        return self.values[column].strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_input(path: Path, label: str, violations: list[str]) -> str:
    try:
        return _sha256(path)
    except OSError:
        violations.append(f"{label} is missing or unreadable")
        return ""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReferenceOnboardingError(f"{label} is not readable JSON") from error
    if not isinstance(payload, dict):
        raise ReferenceOnboardingError(f"{label} must contain a JSON object")
    return payload


def _read_rows(path: Path, violations: list[str]) -> list[_Row]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != HEADERS[path.name]:
                violations.append(
                    f"{path.name}: header is {reader.fieldnames}, "
                    f"expected {list(HEADERS[path.name])}"
                )
                return []
            rows: list[_Row] = []
            for line, values in enumerate(reader, start=2):
                if None in values or any(value is None for value in values.values()):
                    violations.append(f"{path.name}:{line}: malformed CSV row")
                    continue
                if not any(value.strip() for value in values.values()):
                    violations.append(f"{path.name}:{line}: blank CSV row")
                    continue
                rows.append(_Row(line, values))
            return rows
    except (OSError, UnicodeError, csv.Error) as error:
        violations.append(f"{path.name}: could not be read as UTF-8 CSV")
        return []


def _hash_mapping(
    payload: dict[str, Any], field: str, violations: list[str]
) -> dict[str, str]:
    value = payload.get(field)
    if not isinstance(value, dict) or set(value) != set(WORKSHEETS):
        violations.append(f"approval manifest {field} must name the four worksheets")
        return {}
    result: dict[str, str] = {}
    for name, digest in value.items():
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            violations.append(f"approval manifest {field}.{name} is not a SHA-256")
        else:
            result[name] = digest
    return result


def _require_approval(
    source_dir: Path,
    reviewed_dir: Path,
    approval_path: Path,
    source_manifest: dict[str, Any],
    violations: list[str],
) -> dict[str, str]:
    approval = _load_json(approval_path, "approval manifest")
    if approval.get("schema_version") != 1:
        violations.append("approval manifest schema_version must be 1")
    if approval.get("traceability") != TRACEABILITY:
        violations.append(f"approval manifest traceability must be {TRACEABILITY!r}")
    if approval.get("approval_status") not in APPROVAL_STATUSES:
        violations.append(
            f"approval manifest approval_status must be one of {APPROVAL_STATUSES!r}"
        )
    required = ["reviewer", "evidence_reference"]
    if approval.get("approval_status") == RESEARCH_APPROVAL_STATUS:
        required.append("reviewer_role")
    for field in required:
        value = approval.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or (value.strip().startswith("<") and value.strip().endswith(">"))
        ):
            violations.append(f"approval manifest {field} must be reviewed")
    try:
        reviewed_on = approval["reviewed_on"]
        if not isinstance(reviewed_on, str):
            raise TypeError
        date.fromisoformat(reviewed_on)
    except (KeyError, TypeError, ValueError):
        violations.append("approval manifest reviewed_on must use YYYY-MM-DD")

    source_manifest_hash = _hash_input(
        source_dir / SOURCE_MANIFEST, "source review manifest", violations
    )
    if approval.get("source_review_manifest_sha256") != source_manifest_hash:
        violations.append("approval manifest hash for source review manifest does not match")
    if approval.get("source_manifest_sha256") != source_manifest.get(
        "source_manifest_sha256"
    ):
        violations.append("approval manifest source_manifest_sha256 does not match")

    approved_source = _hash_mapping(
        approval, "source_worksheet_sha256", violations
    )
    approved_reviewed = _hash_mapping(
        approval, "reviewed_worksheet_sha256", violations
    )
    input_hashes = {
        f"source/{SOURCE_MANIFEST}": source_manifest_hash,
        "approval_manifest.json": _hash_input(
            approval_path, "approval manifest", violations
        ),
    }
    for name in WORKSHEETS:
        source_hash = _hash_input(
            source_dir / name, f"source worksheet {name}", violations
        )
        reviewed_hash = _hash_input(
            reviewed_dir / name, f"reviewed worksheet {name}", violations
        )
        input_hashes[f"source/{name}"] = source_hash
        input_hashes[f"reviewed/{name}"] = reviewed_hash
        if approved_source.get(name) != source_hash:
            violations.append(
                f"approval manifest hash for source {name} does not match"
            )
        if approved_reviewed.get(name) != reviewed_hash:
            violations.append(
                f"approval manifest hash for reviewed {name} does not match"
            )
    return input_hashes


def _source_rows(
    source_dir: Path,
    source_manifest: dict[str, Any],
    violations: list[str],
) -> dict[str, list[_Row]]:
    if source_manifest.get("traceability") != TRACEABILITY:
        violations.append(f"source review manifest traceability must be {TRACEABILITY!r}")
    if source_manifest.get("status") != "scoped_onboarding_pending_administrator_review":
        violations.append("source review manifest is not the scoped pending snapshot")
    if source_manifest.get("administrator_reviewed") is not False:
        violations.append("source review manifest must remain unreviewed")
    if source_manifest.get("database_writes_performed") is not False:
        violations.append("source review manifest records database writes")
    upstream_hash = source_manifest.get("source_manifest_sha256")
    if not isinstance(upstream_hash, str) or _SHA256.fullmatch(upstream_hash) is None:
        violations.append("source review manifest source_manifest_sha256 is invalid")

    expected_hashes = source_manifest.get("output_sha256")
    expected_counts = source_manifest.get("row_counts")
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != set(WORKSHEETS):
        violations.append("source review manifest must hash the four worksheets")
        expected_hashes = {}
    if not isinstance(expected_counts, dict) or set(expected_counts) != set(WORKSHEETS):
        violations.append("source review manifest must count the four worksheets")
        expected_counts = {}

    result: dict[str, list[_Row]] = {}
    for name in WORKSHEETS:
        path = source_dir / name
        if not path.is_file():
            violations.append(f"source worksheet is missing: {name}")
            result[name] = []
            continue
        digest = _hash_input(path, f"source worksheet {name}", violations)
        if expected_hashes.get(name) != digest:
            violations.append(f"source review manifest hash for {name} does not match")
        rows = _read_rows(path, violations)
        result[name] = rows
        expected_count = expected_counts.get(name)
        if type(expected_count) is not int or expected_count != len(rows):
            violations.append(
                f"source review manifest row count for {name} does not match"
            )
    return result


def _indexed_rows(
    filename: str, rows: list[_Row], violations: list[str]
) -> dict[tuple[str, ...], _Row]:
    indexed: dict[tuple[str, ...], _Row] = {}
    for row in rows:
        key = tuple(row.get(column) for column in KEY_COLUMNS[filename])
        if not all(key):
            violations.append(f"{filename}:{row.line}: source identity is blank")
        elif key in indexed:
            violations.append(f"{filename}:{row.line}: duplicate source identity {key}")
        else:
            indexed[key] = row
    return indexed


def _compare_source(
    filename: str,
    source_rows: list[_Row],
    reviewed_rows: list[_Row],
    violations: list[str],
) -> None:
    source = _indexed_rows(filename, source_rows, violations)
    reviewed = _indexed_rows(filename, reviewed_rows, violations)
    if set(source) != set(reviewed):
        violations.append(f"{filename}: reviewed source identities differ from source")
        return
    for key, source_row in source.items():
        reviewed_row = reviewed[key]
        for column in IMMUTABLE_COLUMNS[filename]:
            if reviewed_row.values[column] != source_row.values[column]:
                violations.append(
                    f"{filename}:{reviewed_row.line}: immutable source column "
                    f"{column} changed"
                )


def _required(
    row: _Row,
    column: str,
    filename: str,
    violations: list[str],
    *,
    max_length: int | None = None,
) -> str:
    value = row.get(column)
    if not value or value == "<TBD>":
        violations.append(f"{filename}:{row.line}: {column} is not reviewed")
    if max_length is not None and len(value) > max_length:
        violations.append(
            f"{filename}:{row.line}: {column} exceeds {max_length} characters"
        )
    return value


def _uuid(
    row: _Row, column: str, filename: str, violations: list[str]
) -> UUID | None:
    raw = _required(row, column, filename, violations)
    try:
        value = UUID(raw)
    except ValueError:
        violations.append(f"{filename}:{row.line}: {column} is not a UUID")
        return None
    if value.int == 0 or str(value) != raw:
        violations.append(
            f"{filename}:{row.line}: {column} must be a non-nil canonical UUID"
        )
        return None
    return value


def _bool(
    row: _Row, column: str, filename: str, violations: list[str]
) -> bool | None:
    raw = row.get(column)
    if raw not in ("true", "false"):
        violations.append(f"{filename}:{row.line}: {column} must be true or false")
        return None
    return raw == "true"


def _positive_int(
    row: _Row, column: str, filename: str, violations: list[str]
) -> int | None:
    raw = row.get(column)
    try:
        value = int(raw)
    except ValueError:
        violations.append(f"{filename}:{row.line}: {column} is not an integer")
        return None
    if value < 1:
        violations.append(f"{filename}:{row.line}: {column} must be positive")
        return None
    return value


def _approval_columns(row: _Row, filename: str, violations: list[str]) -> None:
    if row.get("catalog_membership_decision") != CATALOG_APPROVAL:
        violations.append(
            f"{filename}:{row.line}: catalog_membership_decision must be "
            f"{CATALOG_APPROVAL!r}"
        )
    if row.get("review_status") not in APPROVAL_STATUSES:
        violations.append(
            f"{filename}:{row.line}: review_status must be one of {APPROVAL_STATUSES!r}"
        )


def _deduplicate(
    values: list[Any], key, label: str, violations: list[str]
) -> tuple[Any, ...]:
    unique: dict[Any, Any] = {}
    for value in values:
        identity = key(value)
        current = unique.get(identity)
        if current is not None and current != value:
            violations.append(f"{label} maps to conflicting reviewed values: {identity}")
        else:
            unique[identity] = value
    return tuple(unique[identity] for identity in sorted(unique, key=str))


def _faculty(rows: list[_Row], violations: list[str]) -> tuple[FacultyReference, ...]:
    filename = WORKSHEETS[0]
    values: list[FacultyReference] = []
    for row in rows:
        _approval_columns(row, filename, violations)
        research_id = _uuid(
            row, "research_faculty_key", filename, violations
        )
        faculty_id = _uuid(row, "faculty_id", filename, violations)
        full_name = _required(
            row, "reviewed_full_name", filename, violations, max_length=150
        )
        full_time = _bool(row, "is_full_time", filename, violations)
        if row.get("availability_term_id") or row.get("availability_time_slot_ids"):
            violations.append(
                f"{filename}:{row.line}: availability fields must stay blank"
            )
        if row.get("required_by_frozen_72_rules") != "true":
            violations.append(
                f"{filename}:{row.line}: row is not required by the frozen 72 rules"
            )
        if research_id is not None and faculty_id == research_id:
            violations.append(
                f"{filename}:{row.line}: faculty_id must not copy the research faculty key"
            )
        if faculty_id is not None and full_time is not None and full_name:
            values.append(FacultyReference(faculty_id, full_name, full_time))
    return _deduplicate(
        values, lambda item: item.faculty_id, "faculty_id", violations
    )


def _subjects(rows: list[_Row], violations: list[str]) -> tuple[SubjectReference, ...]:
    filename = WORKSHEETS[1]
    values: list[SubjectReference] = []
    for row in rows:
        _approval_columns(row, filename, violations)
        code = _required(
            row, "proposed_subject_code", filename, violations, max_length=20
        )
        title = _required(
            row, "proposed_subject_title", filename, violations, max_length=150
        )
        units = _positive_int(row, "proposed_units", filename, violations)
        try:
            subject_type = SubjectType(row.get("proposed_subject_type"))
        except ValueError:
            violations.append(
                f"{filename}:{row.line}: proposed_subject_type must be LAB or LEC"
            )
            subject_type = None
        if code and title and units is not None and subject_type is not None:
            values.append(SubjectReference(code, title, units, subject_type))
    return _deduplicate(
        values, lambda item: item.subject_code, "subject_code", violations
    )


def _rooms(
    rows: list[_Row], violations: list[str]
) -> tuple[tuple[BuildingReference, ...], tuple[RoomReference, ...]]:
    filename = WORKSHEETS[2]
    buildings: list[BuildingReference] = []
    rooms: list[RoomReference] = []
    for row in rows:
        if (
            row.get("catalog_membership_decision") == ROOM_EXCLUSION
            and row.get("review_status") in APPROVAL_STATUSES
        ):
            # A reviewed non-physical room (for example a virtual section label) is
            # not onboarded; the packager must drop rules that reference it.
            continue
        _approval_columns(row, filename, violations)
        room_code = _required(
            row, "reviewed_room_code", filename, violations, max_length=20
        )
        building_id = _uuid(row, "building_id", filename, violations)
        building_name = _required(
            row, "building_name", filename, violations, max_length=100
        )
        building_code = _required(
            row, "building_code", filename, violations, max_length=20
        )
        if building_code != building_code.upper():
            violations.append(
                f"{filename}:{row.line}: building_code must be uppercase"
            )
        try:
            room_type = RoomType(row.get("room_type"))
        except ValueError:
            violations.append(
                f"{filename}:{row.line}: room_type must be lecture or laboratory"
            )
            room_type = None
        expanded = _bool(row, "expanded", filename, violations)
        capacity = _positive_int(
            row, "derived_capacity_after_type_review", filename, violations
        )
        if room_type is not None and expanded is not None and capacity is not None:
            expected = derive_room_capacity(room_type, expanded)
            if capacity != expected:
                violations.append(
                    f"{filename}:{row.line}: derived capacity is {capacity}, "
                    f"expected {expected}"
                )
        if (
            room_code
            and building_id is not None
            and building_name
            and building_code
            and room_type is not None
            and expanded is not None
            and capacity is not None
        ):
            buildings.append(
                BuildingReference(building_id, building_name, building_code)
            )
            rooms.append(
                RoomReference(
                    room_code,
                    building_id,
                    building_name,
                    capacity,
                    room_type,
                    expanded,
                )
            )

    unique_buildings = _deduplicate(
        buildings, lambda item: item.building_id, "building_id", violations
    )
    _deduplicate(
        list(unique_buildings), lambda item: item.building_code, "building_code", violations
    )
    _deduplicate(
        list(unique_buildings),
        lambda item: item.building_name.casefold(),
        "building_name",
        violations,
    )
    unique_rooms = _deduplicate(
        rooms, lambda item: item.room_code, "room_code", violations
    )
    return unique_buildings, unique_rooms


def _terms(rows: list[_Row], violations: list[str]) -> tuple[TermReference, ...]:
    filename = WORKSHEETS[3]
    values: list[TermReference] = []
    for row in rows:
        if row.get("review_status") not in APPROVAL_STATUSES:
            violations.append(
                f"{filename}:{row.line}: review_status must be one of "
                f"{APPROVAL_STATUSES!r}"
            )
        if row.get("finalized_history_approval") != HISTORY_APPROVAL:
            violations.append(
                f"{filename}:{row.line}: finalized_history_approval must be "
                f"{HISTORY_APPROVAL!r}"
            )
        if row.get("proposed_is_active") != "false":
            violations.append(
                f"{filename}:{row.line}: proposed_is_active must remain false"
            )
        if row.get("frozen_research_partition") not in {"training", "frozen_training"}:
            violations.append(
                f"{filename}:{row.line}: term must come from the training partition"
            )
        term_id = _uuid(row, "term_id", filename, violations)
        academic_year = row.get("academic_year")
        try:
            academic_year_start(academic_year)
        except ValueError as error:
            violations.append(f"{filename}:{row.line}: {error}")
        term_number = _positive_int(row, "term_number", filename, violations)
        if term_id is not None and term_number is not None:
            values.append(TermReference(term_id, academic_year, term_number))

    unique = _deduplicate(values, lambda item: item.term_id, "term_id", violations)
    _deduplicate(
        list(unique),
        lambda item: (item.academic_year, item.term_number),
        "academic year and term number",
        violations,
    )
    return unique


def load_reviewed_reference_data(
    source_dir: Path, reviewed_dir: Path, approval_manifest: Path
) -> ReviewedReferenceData:
    """Load an approved copy without trusting edits to historical evidence."""

    source_dir = source_dir.resolve()
    reviewed_dir = reviewed_dir.resolve()
    approval_manifest = approval_manifest.resolve()
    if source_dir == reviewed_dir:
        raise ReferenceOnboardingError(
            "reviewed worksheets must be separate from the pending source snapshot"
        )
    source_manifest_path = source_dir / SOURCE_MANIFEST
    source_manifest = _load_json(source_manifest_path, "source review manifest")
    violations: list[str] = []
    source = _source_rows(source_dir, source_manifest, violations)

    reviewed: dict[str, list[_Row]] = {}
    for name in WORKSHEETS:
        path = reviewed_dir / name
        if not path.is_file():
            violations.append(f"reviewed worksheet is missing: {name}")
            reviewed[name] = []
        else:
            reviewed[name] = _read_rows(path, violations)
        _compare_source(name, source[name], reviewed[name], violations)

    input_hashes = _require_approval(
        source_dir,
        reviewed_dir,
        approval_manifest,
        source_manifest,
        violations,
    )
    status = _load_json(approval_manifest, "approval manifest").get("approval_status")
    for name in WORKSHEETS:
        for row in reviewed[name]:
            if row.get("review_status") in APPROVAL_STATUSES and row.get(
                "review_status"
            ) != status:
                violations.append(
                    f"{name}:{row.line}: review_status must match the approval "
                    "manifest approval_status"
                )
    faculty = _faculty(reviewed[WORKSHEETS[0]], violations)
    subjects = _subjects(reviewed[WORKSHEETS[1]], violations)
    buildings, rooms = _rooms(reviewed[WORKSHEETS[2]], violations)
    terms = _terms(reviewed[WORKSHEETS[3]], violations)
    if violations:
        raise ReferenceOnboardingError(violations)
    return ReviewedReferenceData(
        faculty=faculty,
        subjects=subjects,
        buildings=buildings,
        rooms=rooms,
        terms=terms,
        row_counts={name: len(reviewed[name]) for name in WORKSHEETS},
        input_sha256=input_hashes,
    )


_REPORT_KEYS = (
    "faculty",
    "subjects",
    "buildings",
    "rooms",
    "terms",
    "time_slots",
)


def _empty_counts() -> dict[str, int]:
    return {name: 0 for name in _REPORT_KEYS}


def _require_postgresql(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        raise ReferenceOnboardingError(
            "reviewed reference onboarding requires local PostgreSQL 17"
        )


def _require_postgresql_17(db: Session) -> None:
    _require_postgresql(db)
    version = db.scalar(text("SHOW server_version_num"))
    if version is None or int(version) // 10000 != 17:
        raise ReferenceOnboardingError(
            "reviewed reference onboarding requires local PostgreSQL 17"
        )


def _same(instance: object, expected: dict[str, object]) -> bool:
    return all(getattr(instance, field) == value for field, value in expected.items())


def _canonical_slots_match(db: Session, term_id: UUID) -> bool:
    actual = list(
        db.execute(
            select(
                TimeSlot.time_slot_id,
                TimeSlot.label,
                TimeSlot.start_time,
                TimeSlot.end_time,
                TimeSlot.ordinal,
            )
            .where(TimeSlot.term_id == term_id)
            .order_by(TimeSlot.ordinal, TimeSlot.time_slot_id)
        ).all()
    )
    expected = [
        (
            window.slot_id(term_id),
            window.label,
            window.start_time,
            window.end_time,
            window.ordinal,
        )
        for window in generate_windows()
    ]
    return actual == expected


def validate_reviewed_references(
    db: Session, data: ReviewedReferenceData
) -> ReferenceOnboardingReport:
    """Plan inserts and reject every differing collision without staging writes."""

    _require_postgresql_17(db)
    created = _empty_counts()
    reused = _empty_counts()
    violations: list[str] = []

    for reference in data.faculty:
        existing = db.get(Faculty, reference.faculty_id)
        if existing is None:
            created["faculty"] += 1
        elif _same(
            existing,
            {
                "full_name": reference.full_name,
                "is_full_time": reference.is_full_time,
                "availability_mask": "",
                "availability_reviewed_term_id": None,
            },
        ):
            reused["faculty"] += 1
        else:
            violations.append(f"faculty {reference.faculty_id} differs from review")

    for reference in data.subjects:
        existing = db.get(Subject, reference.subject_code)
        if existing is None:
            created["subjects"] += 1
        elif _same(
            existing,
            {
                "subject_title": reference.subject_title,
                "units": reference.units,
                "subject_type": reference.subject_type.value,
            },
        ):
            reused["subjects"] += 1
        else:
            violations.append(f"subject {reference.subject_code} differs from review")

    for reference in data.buildings:
        existing = db.get(Building, reference.building_id)
        code_owner = db.scalar(
            select(Building).where(Building.building_code == reference.building_code)
        )
        name_owner = db.scalar(
            select(Building).where(
                func.lower(Building.building_name)
                == reference.building_name.lower()
            )
        )
        alternate = next(
            (
                item
                for item in (code_owner, name_owner)
                if item is not None and item.building_id != reference.building_id
            ),
            None,
        )
        if alternate is not None:
            violations.append(
                f"building {reference.building_id} conflicts with existing code or name"
            )
        elif existing is None:
            created["buildings"] += 1
        elif _same(
            existing,
            {
                "building_name": reference.building_name,
                "building_code": reference.building_code,
            },
        ):
            reused["buildings"] += 1
        else:
            violations.append(f"building {reference.building_id} differs from review")

    for reference in data.rooms:
        existing = db.get(Room, reference.room_code)
        if existing is None:
            created["rooms"] += 1
        elif _same(
            existing,
            {
                "building_id": reference.building_id,
                "building_name": reference.building_name,
                "capacity": reference.capacity,
                "room_type": reference.room_type.value,
                "expanded": reference.expanded,
            },
        ):
            reused["rooms"] += 1
        else:
            violations.append(f"room {reference.room_code} differs from review")

    slot_count = len(generate_windows())
    for reference in data.terms:
        if reference.is_active:
            violations.append(f"term {reference.term_id} must remain inactive")
        existing = db.get(Term, reference.term_id)
        natural_owner = db.scalar(
            select(Term).where(
                Term.academic_year == reference.academic_year,
                Term.term_number == reference.term_number,
            )
        )
        if natural_owner is not None and natural_owner.term_id != reference.term_id:
            violations.append(
                f"term {reference.academic_year} T{reference.term_number} "
                "already has another term_id"
            )
        elif existing is None:
            created["terms"] += 1
            created["time_slots"] += slot_count
        elif not _same(
            existing,
            {
                "academic_year": reference.academic_year,
                "term_number": reference.term_number,
                "is_active": False,
            },
        ):
            violations.append(f"term {reference.term_id} differs from review")
        elif not _canonical_slots_match(db, reference.term_id):
            violations.append(
                f"term {reference.term_id} does not have the canonical time-slot grid"
            )
        else:
            reused["terms"] += 1
            reused["time_slots"] += slot_count

    if violations:
        raise ReferenceOnboardingError(violations)
    return ReferenceOnboardingReport(created=created, reused=reused)


def _lock_reference_tables(db: Session) -> None:
    """Serialize all natural-key checks before the first insert."""

    db.execute(
        text(
            "LOCK TABLE faculty, subject, building, room, term, time_slot "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def onboard_reviewed_references(
    db: Session,
    data: ReviewedReferenceData,
    *,
    receipt: dict[str, Any] | None = None,
) -> ReferenceOnboardingReport:
    """Stage the reviewed catalog atomically; the caller owns commit or rollback."""

    _require_postgresql(db)
    _lock_reference_tables(db)
    _require_postgresql_17(db)
    if receipt is not None:
        require_matching_receipt(receipt, data, load_database_state(db))
    report = validate_reviewed_references(db, data)
    if receipt is not None and receipt.get("planned_changes") != _planned_changes(
        data, report
    ):
        raise ReferenceOnboardingError(
            "planned catalog changes differ from the validation receipt"
        )

    for reference in data.faculty:
        if db.get(Faculty, reference.faculty_id) is None:
            db.add(
                Faculty(
                    faculty_id=reference.faculty_id,
                    full_name=reference.full_name,
                    is_full_time=reference.is_full_time,
                    availability_mask="",
                    availability_reviewed_term_id=None,
                )
            )
    for reference in data.subjects:
        if db.get(Subject, reference.subject_code) is None:
            db.add(
                Subject(
                    subject_code=reference.subject_code,
                    subject_title=reference.subject_title,
                    units=reference.units,
                    subject_type=reference.subject_type.value,
                )
            )
    for reference in data.buildings:
        if db.get(Building, reference.building_id) is None:
            db.add(
                Building(
                    building_id=reference.building_id,
                    building_name=reference.building_name,
                    building_code=reference.building_code,
                )
            )
    db.flush()
    for reference in data.rooms:
        if db.get(Room, reference.room_code) is None:
            db.add(
                Room(
                    room_code=reference.room_code,
                    building_id=reference.building_id,
                    building_name=reference.building_name,
                    capacity=reference.capacity,
                    room_type=reference.room_type.value,
                    expanded=reference.expanded,
                )
            )
    for reference in data.terms:
        if db.get(Term, reference.term_id) is None:
            db.add(
                Term(
                    term_id=reference.term_id,
                    academic_year=reference.academic_year,
                    term_number=reference.term_number,
                    is_active=False,
                )
            )
            db.flush()
            ensure_canonical_slots(db, reference.term_id)
    db.flush()
    return report


def _catalog_payload(db: Session) -> tuple[dict[str, int], list[object]]:
    statements = {
        "faculty": select(
            Faculty.faculty_id,
            Faculty.full_name,
            Faculty.is_full_time,
            Faculty.availability_mask,
            Faculty.availability_reviewed_term_id,
        ).order_by(Faculty.faculty_id),
        "subjects": select(
            Subject.subject_code,
            Subject.subject_title,
            Subject.units,
            Subject.subject_type,
        ).order_by(Subject.subject_code),
        "buildings": select(
            Building.building_id,
            Building.building_name,
            Building.building_code,
        ).order_by(Building.building_id),
        "rooms": select(
            Room.room_code,
            Room.building_id,
            Room.building_name,
            Room.capacity,
            Room.room_type,
            Room.expanded,
        ).order_by(Room.room_code),
        "terms": select(
            Term.term_id,
            Term.academic_year,
            Term.term_number,
            Term.is_active,
        ).order_by(Term.term_id),
        "time_slots": select(
            TimeSlot.time_slot_id,
            TimeSlot.term_id,
            TimeSlot.label,
            TimeSlot.start_time,
            TimeSlot.end_time,
            TimeSlot.ordinal,
        ).order_by(TimeSlot.term_id, TimeSlot.ordinal, TimeSlot.time_slot_id),
    }
    counts: dict[str, int] = {}
    payload: list[object] = []
    for name, statement in statements.items():
        rows = [tuple(str(value) for value in row) for row in db.execute(statement)]
        counts[name] = len(rows)
        payload.append([name, rows])
    return counts, payload


def load_database_state(db: Session) -> ReferenceDatabaseState:
    """Fingerprint the exact PostgreSQL catalog snapshot a reviewer validated."""

    _require_postgresql_17(db)
    database_row = db.execute(
        text(
            "SELECT current_database(), current_schema(), "
            "current_setting('server_version_num'), "
            "(SELECT system_identifier::text FROM pg_control_system())"
        )
    ).one()
    revisions = tuple(db.scalars(text("SELECT version_num FROM alembic_version")))
    if len(revisions) != 1:
        raise ReferenceOnboardingError(
            "database must have exactly one current Alembic revision"
        )
    counts, payload = _catalog_payload(db)
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return ReferenceDatabaseState(
        database={
            "name": str(database_row[0]),
            "schema": str(database_row[1]),
            "server_version_num": str(database_row[2]),
            "system_identifier": str(database_row[3]),
        },
        alembic_revision=revisions[0],
        catalog_counts=counts,
        catalog_sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    )


def build_validation_receipt(
    data: ReviewedReferenceData,
    state: ReferenceDatabaseState,
    report: ReferenceOnboardingReport,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "traceability": TRACEABILITY,
        "action": "validated_read_only",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "transaction": {
            "read_only": True,
            "isolation_level": "repeatable read",
        },
        "input_sha256": dict(data.input_sha256),
        "database": dict(state.database),
        "alembic_revision": state.alembic_revision,
        "catalog_state": {
            "counts": dict(state.catalog_counts),
            "sha256": state.catalog_sha256,
        },
        "planned_changes": _planned_changes(data, report),
        "rules_packaged_imported_or_activated": False,
    }


def _planned_changes(
    data: ReviewedReferenceData, report: ReferenceOnboardingReport
) -> dict[str, dict[str, int]]:
    return {
        "created": dict(report.created),
        "reused": dict(report.reused),
        "worksheet_rows": dict(data.row_counts),
    }


def require_matching_receipt(
    receipt: dict[str, Any],
    data: ReviewedReferenceData,
    state: ReferenceDatabaseState,
) -> None:
    violations: list[str] = []
    if receipt.get("schema_version") != 1:
        violations.append("review receipt schema_version must be 1")
    if receipt.get("traceability") != TRACEABILITY:
        violations.append("review receipt traceability differs")
    if receipt.get("action") != "validated_read_only":
        violations.append("review receipt was not produced by read-only validation")
    if receipt.get("transaction") != {
        "read_only": True,
        "isolation_level": "repeatable read",
    }:
        violations.append("review receipt does not record the required transaction")
    if receipt.get("rules_packaged_imported_or_activated") is not False:
        violations.append("review receipt must not package, import or activate rules")
    if receipt.get("input_sha256") != data.input_sha256:
        violations.append("reviewed inputs changed after validation")
    if receipt.get("database") != state.database:
        violations.append("database identity changed after validation")
    if receipt.get("alembic_revision") != state.alembic_revision:
        violations.append("Alembic revision changed after validation")
    expected_catalog = {
        "counts": state.catalog_counts,
        "sha256": state.catalog_sha256,
    }
    if receipt.get("catalog_state") != expected_catalog:
        violations.append("catalog state changed after validation")
    if violations:
        raise ReferenceOnboardingError(violations)


__all__ = [
    "APPROVAL_STATUS",
    "BuildingReference",
    "CATALOG_APPROVAL",
    "FacultyReference",
    "RESEARCH_APPROVAL_STATUS",
    "REVIEW_STATUS",
    "ReferenceDatabaseState",
    "ReferenceOnboardingReport",
    "ReferenceOnboardingError",
    "ReviewedReferenceData",
    "RoomReference",
    "SubjectReference",
    "TermReference",
    "build_validation_receipt",
    "load_reviewed_reference_data",
    "load_database_state",
    "onboard_reviewed_references",
    "require_matching_receipt",
    "validate_reviewed_references",
]
