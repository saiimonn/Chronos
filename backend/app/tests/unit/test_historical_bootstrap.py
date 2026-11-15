"""CHR-108 / CHR-ING-001 synthetic file validation and database-free CLI tests."""

import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.cli import import_history
from app.services.ingestion.historical_file import (
    BUNDLE_FILES,
    HistoricalImportError,
    HistoricalImportRecord,
    HistoricalRowRejection,
    load_historical_import,
    parse_historical_import,
)
from app.tests.support.historical_bootstrap import (
    FACULTY_ID,
    FACULTY_NAME,
    refresh_manifest,
    rewrite_as_teacher_subject_tier,
    write_bootstrap_dataset,
)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    connect = Mock(
        side_effect=AssertionError("unit tests must not connect to a database")
    )
    monkeypatch.setattr(Engine, "connect", connect)
    monkeypatch.setattr(Engine, "raw_connection", connect)
    return connect


@pytest.fixture
def bootstrap_files(tmp_path: Path) -> tuple[Path, Path]:
    return write_bootstrap_dataset(tmp_path)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_loader_maps_local_faculty_and_preserves_history_without_empty_snapshots(
    bootstrap_files,
):
    dataset_dir, crosswalk = bootstrap_files
    workbook_hash = hashlib.sha256(
        b"CHR-108 / CHR-ING-001 synthetic workbook"
    ).hexdigest()

    data = load_historical_import(dataset_dir, crosswalk)

    assert data.records == tuple(
        HistoricalImportRecord(
            historical_record_id=uuid5(
                NAMESPACE_URL,
                f"chronos:{workbook_hash}:SYNTHETIC TERM {term}:{term * 10}:{term * 10 + 1}",
            ),
            academic_year="2023-2024",
            term_number=term,
            source_faculty_id=FACULTY_ID,
            faculty_name=FACULTY_NAME,
            subject_code="SUBJ-1",
            subject_title="Synthetic Subject A",
            room_code="ROOM-1",
            meeting_days=days,
            time_slot=slot,
        )
        for term, days, slot in ((1, "MWF", "08:00-09:30"), (2, "TTh", "13:00-14:30"))
    )
    assert data.periods == (("2023-2024", 1), ("2023-2024", 2))
    assert data.empty_periods == (("2023-2024", 3),)
    assert dict(data.input_sha256) == {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in (
            ("canonical_records.csv", dataset_dir / "canonical_records.csv"),
            ("term_manifest.csv", dataset_dir / "term_manifest.csv"),
            ("source_manifest.json", dataset_dir / "source_manifest.json"),
            ("faculty_crosswalk.csv", crosswalk),
        )
    }


def test_fixture_is_deterministic_and_accepts_a_caller_supplied_faculty_id(
    tmp_path: Path,
):
    faculty_id = UUID("20000000-0000-0000-0000-000000000109")
    first, first_crosswalk = write_bootstrap_dataset(tmp_path / "first", faculty_id)
    second, second_crosswalk = write_bootstrap_dataset(tmp_path / "second", faculty_id)

    for name in ("canonical_records.csv", "term_manifest.csv", "source_manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert first_crosswalk.read_bytes() == second_crosswalk.read_bytes()
    assert _read_csv(first_crosswalk)[0] == ["faculty_name", "faculty_id"]
    assert {
        record.source_faculty_id
        for record in load_historical_import(first, first_crosswalk).records
    } == {faculty_id}


@pytest.mark.parametrize("filename", ["canonical_records.csv", "term_manifest.csv"])
def test_loader_rejects_raw_hash_tampering(bootstrap_files, filename):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / filename
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(
        HistoricalImportError, match=f"{filename}: source-manifest hash mismatch"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "filename", ["canonical_records.csv", "term_manifest.csv", "faculty_crosswalk.csv"]
)
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_column", "invalid or missing CSV columns"),
        ("duplicate_column", "invalid or missing CSV columns"),
        ("missing_header", "invalid or missing CSV columns"),
        ("short_row", "malformed CSV row"),
        ("long_row", "malformed CSV row"),
        ("invalid_syntax", "invalid CSV encoding or syntax"),
        ("invalid_encoding", "invalid CSV encoding or syntax"),
    ],
)
def test_loader_rejects_malformed_csv(bootstrap_files, filename, mutation, message):
    dataset_dir, crosswalk = bootstrap_files
    path = crosswalk if filename == "faculty_crosswalk.csv" else dataset_dir / filename
    with path.open(encoding="utf-8", newline="") as source:
        table = list(csv.reader(source))
    if mutation == "invalid_syntax":
        path.write_text(",".join(table[0]) + '\n"unterminated\n', encoding="utf-8")
    elif mutation == "invalid_encoding":
        path.write_bytes(path.read_bytes() + b"\xff")
    else:
        if mutation == "missing_column":
            table = [row[:-1] for row in table]
        elif mutation == "duplicate_column":
            table[0][-1] = table[0][0]
        elif mutation == "missing_header":
            table = table[1:]
        elif mutation == "short_row":
            table[1].pop()
        else:
            table[1].append("EXTRA SYNTHETIC VALUE")
        with path.open("w", encoding="utf-8", newline="") as destination:
            csv.writer(destination, lineterminator="\n").writerows(table)
    refresh_manifest(dataset_dir)

    with pytest.raises(HistoricalImportError, match=f"{filename}.*{message}"):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("filename", ["term_manifest.csv", "faculty_crosswalk.csv"])
def test_loader_requires_exact_decision_and_crosswalk_columns(
    bootstrap_files, filename
):
    dataset_dir, crosswalk = bootstrap_files
    path = crosswalk if filename == "faculty_crosswalk.csv" else dataset_dir / filename
    fields, rows = _read_csv(path)
    for row in rows:
        row["unexpected_column"] = "SYNTHETIC"
    _write_csv(path, fields + ["unexpected_column"], rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match=f"{filename}: unexpected CSV columns"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("faculty_name", ""),
        ("faculty_name", "   "),
        ("faculty_name", "<TBD>"),
        ("faculty_name", "X" * 151),
        ("faculty_id", "not-a-uuid"),
        ("faculty_id", "<TBD>"),
        ("faculty_id", str(UUID(int=0))),
        (
            "faculty_id",
            str(uuid5(NAMESPACE_URL, f"chronos:historical-faculty:{FACULTY_NAME}")),
        ),
    ],
)
def test_loader_rejects_invalid_reviewed_mapping(bootstrap_files, field, value):
    dataset_dir, crosswalk = bootstrap_files
    fields, rows = _read_csv(crosswalk)
    rows[0][field] = value
    _write_csv(crosswalk, fields, rows)

    with pytest.raises(
        HistoricalImportError,
        match="faculty_crosswalk.csv:2: invalid or duplicate reviewed mapping",
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_loader_requires_a_mapping_for_every_historical_name(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    _write_csv(crosswalk, ["faculty_name", "faculty_id"], [])

    with pytest.raises(HistoricalImportError, match="missing reviewed faculty mapping"):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "faculty_id", [FACULTY_ID, UUID("20000000-0000-0000-0000-000000000109")]
)
def test_loader_rejects_duplicate_normalized_mapping_names(bootstrap_files, faculty_id):
    dataset_dir, crosswalk = bootstrap_files
    fields, rows = _read_csv(crosswalk)
    rows.append({"faculty_name": " faculty\t a ", "faculty_id": str(faculty_id)})
    _write_csv(crosswalk, fields, rows)

    with pytest.raises(
        HistoricalImportError,
        match="faculty_crosswalk.csv:3: invalid or duplicate reviewed mapping",
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_loader_preserves_source_names_after_mapping_normalization(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    fields, rows = _read_csv(crosswalk)
    rows[0]["faculty_name"] = " faculty\t a "
    _write_csv(crosswalk, fields, rows)
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0]["faculty_name"] = " Faculty   A "
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    data = load_historical_import(dataset_dir, crosswalk)

    assert [record.faculty_name for record in data.records] == [
        " Faculty   A ",
        FACULTY_NAME,
    ]
    assert {record.source_faculty_id for record in data.records} == {FACULTY_ID}


def test_loader_allows_reviewed_name_aliases_to_share_a_local_id(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    fields, rows = _read_csv(crosswalk)
    rows.append({"faculty_name": "FACULTY ALIAS A", "faculty_id": str(FACULTY_ID)})
    _write_csv(crosswalk, fields, rows)
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[1]["faculty_name"] = "FACULTY ALIAS A"
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    data = load_historical_import(dataset_dir, crosswalk)

    assert [record.faculty_name for record in data.records] == [
        FACULTY_NAME,
        "FACULTY ALIAS A",
    ]
    assert {record.source_faculty_id for record in data.records} == {FACULTY_ID}


def _under_new_source_identity(row: dict[str, str], source_row: int) -> dict[str, str]:
    """The same row as read from another physical source row, with the record
    identity the preprocessor would derive for it."""
    copy = row | {"source_row": str(source_row), "offering_source_row": str(source_row)}
    copy["historical_record_id"] = str(
        uuid5(
            NAMESPACE_URL,
            f"chronos:{copy['source_workbook_sha256']}:{copy['source_sheet']}:"
            f"{source_row}:{source_row}",
        )
    )
    return copy


def _append_records(dataset_dir: Path, *extra: dict[str, str]) -> None:
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows.extend(extra)
    _write_csv(path, fields, rows)
    manifest_path = dataset_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["accepted_record_count"] = len(rows)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refresh_manifest(dataset_dir)


@pytest.mark.parametrize("tier", ["full", "older", "older_without_placement"])
def test_loader_rejects_an_observation_copied_under_another_source_identity(
    bootstrap_files, tier,
):
    dataset_dir, crosswalk = bootstrap_files
    if tier != "full":
        rewrite_as_teacher_subject_tier(
            dataset_dir, keep_placement=tier == "older",
        )
    _, rows = _read_csv(dataset_dir / "canonical_records.csv")
    _append_records(dataset_dir, _under_new_source_identity(rows[0], 99))

    with pytest.raises(
        HistoricalImportError,
        match="canonical_records.csv:4: invalid historical record.*duplicate teaching observation",
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_loader_keeps_distinct_offerings_with_identical_placements(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    _, rows = _read_csv(dataset_dir / "canonical_records.csv")
    other_group = _under_new_source_identity(rows[0], 99) | {"group_number": "2"}
    _append_records(dataset_dir, other_group)

    data = load_historical_import(dataset_dir, crosswalk)

    assert len(data.records) == 3
    assert {record.historical_record_id for record in data.records} >= {
        UUID(rows[0]["historical_record_id"]),
        UUID(other_group["historical_record_id"]),
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("historical_record_id", "not-a-uuid", "invalid source record UUID"),
        (
            "historical_record_id",
            str(UUID(int=0)),
            "invalid or duplicate source record identity",
        ),
        (
            "historical_record_id",
            str(FACULTY_ID),
            "invalid or duplicate source record identity",
        ),
        ("source_workbook_sha256", "0" * 64, "source workbook commitment differs"),
        ("source_row", "0", "source row must be positive"),
        ("offering_source_row", "-1", "source row must be positive"),
        ("source_row", "not-a-row", "invalid source row number"),
        ("offering_source_row", "10.5", "invalid source row number"),
        ("source_row", "12", "invalid or duplicate source record identity"),
        ("offering_source_row", "9", "invalid or duplicate source record identity"),
        (
            "source_sheet",
            "UNKNOWN SYNTHETIC SHEET",
            "period is not complete and included",
        ),
        ("academic_year", "2023/2024", "invalid academic-year format"),
        ("academic_year", "2023-2025", "invalid academic-year format"),
        ("term_number", "0", "term_number must be"),
        ("term_number", "4", "term_number must be"),
        ("term_number", "01", "term_number must be"),
        ("term_number", "2", "period is not complete and included"),
        ("group_number", "", "invalid group number"),
        ("group_number", "0", "invalid group number"),
        ("group_number", "G1", "invalid group number"),
    ],
)
def test_loader_rejects_invalid_record_identity_provenance_or_period(
    bootstrap_files, field, value, message
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError,
        match=f"canonical_records.csv:2: invalid historical record.*{message}",
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "different_meeting", [False, True], ids=["duplicate_record", "duplicate_provenance"]
)
def test_loader_rejects_duplicate_source_records(bootstrap_files, different_meeting):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    duplicate = rows[0].copy()
    if different_meeting:
        duplicate.update(meeting_days="TTh", start_time="15:00", end_time="16:30")
    rows.append(duplicate)
    _write_csv(path, fields, rows)
    manifest_path = dataset_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["accepted_record_count"] = len(rows)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="invalid or duplicate source record identity"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sheet", ""),
        ("source_sheet", "<TBD>"),
        ("source_sheet", "X" * 201),
        ("academic_year", "2023/2024"),
        ("academic_year", "2023-2025"),
        ("term_number", "0"),
        ("term_number", "4"),
        ("complete", "false"),
        ("complete", "1"),
        ("include_in_research", "1"),
    ],
)
def test_loader_rejects_invalid_or_incomplete_included_terms(
    bootstrap_files, field, value
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "term_manifest.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="term_manifest.csv:2: invalid term decision"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("field", ["source_sheet", "term_number"])
def test_loader_rejects_duplicate_term_sheets_or_periods(bootstrap_files, field):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "term_manifest.csv"
    fields, rows = _read_csv(path)
    rows[1][field] = rows[0][field]
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="term_manifest.csv:3: invalid term decision"
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_loader_rejects_records_from_an_excluded_term(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "term_manifest.csv"
    fields, rows = _read_csv(path)
    rows[0]["include_in_research"] = "false"
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="period is not complete and included"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("days", ["", "X", "MM", "WM", "TH", "M W", "SaTh"])
def test_loader_rejects_invalid_canonical_days(bootstrap_files, days):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0]["meeting_days"] = days
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError,
        match="blank, placeholder, or oversized value|invalid canonical meeting days",
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_time", "8:00"),
        ("start_time", "24:00"),
        ("start_time", "08:60"),
        ("start_time", "08:00 AM"),
        ("start_time", ""),
        ("end_time", "24:00"),
        ("end_time", "09:60"),
        ("end_time", "08:00"),
        ("end_time", "07:59"),
        ("end_time", "09:30:00"),
    ],
)
def test_loader_rejects_invalid_or_nonincreasing_24_hour_intervals(
    bootstrap_files, field, value
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(HistoricalImportError, match="invalid historical time interval"):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("field", ["teacher_subject_eligible", "full_stream_eligible"])
@pytest.mark.parametrize("value", ["0", "2", "true", ""])
def test_loader_rejects_invalid_or_policy_inconsistent_eligibility_flags(
    bootstrap_files, field, value
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="historical eligibility flag differs from policy"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("academic_year", ["2017-2018", "2022-2023"])
def test_loader_checks_flag_eligibility_against_the_academic_year(
    bootstrap_files, academic_year
):
    dataset_dir, crosswalk = bootstrap_files
    for filename in ("canonical_records.csv", "term_manifest.csv"):
        path = dataset_dir / filename
        fields, rows = _read_csv(path)
        for row in rows:
            row["academic_year"] = academic_year
        _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="historical eligibility flag differs from policy"
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_teacher_subject_tier_needs_only_teacher_subject_and_term(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    rewrite_as_teacher_subject_tier(dataset_dir, keep_placement=False)

    data = load_historical_import(dataset_dir, crosswalk)

    assert len(data.records) == 2
    for record in data.records:
        assert (record.academic_year, record.subject_code) == ("2019-2020", "SUBJ-1")
        assert record.source_faculty_id == FACULTY_ID
        assert (record.meeting_days, record.time_slot, record.room_code) == (
            None,
            None,
            None,
        )


def test_teacher_subject_tier_does_not_retain_a_supplied_placement(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    rewrite_as_teacher_subject_tier(dataset_dir, keep_placement=True)

    data = load_historical_import(dataset_dir, crosswalk)

    assert {
        (record.meeting_days, record.time_slot, record.room_code)
        for record in data.records
    } == {(None, None, None)}


@pytest.mark.parametrize("field,value", [
    ("meeting_days", "Sa"), ("start_time", "08:30"),
    ("end_time", "10:00"), ("room_code", "ROOM-2"),
])
def test_teacher_subject_tier_keeps_distinct_continuation_meetings(tmp_path, field, value):
    dataset_dir, crosswalk = write_bootstrap_dataset(tmp_path, continuation=True)
    rewrite_as_teacher_subject_tier(dataset_dir, keep_placement=True)
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    for placement in ("meeting_days", "start_time", "end_time", "room_code"):
        rows[2][placement] = rows[0][placement]
    rows[2][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    data = load_historical_import(dataset_dir, crosswalk)

    assert len(data.records) == 3
    assert len({record.historical_record_id for record in data.records}) == 3
    assert sum(record.term_number == 1 for record in data.records) == 2
    assert {
        (record.meeting_days, record.time_slot, record.room_code)
        for record in data.records
    } == {(None, None, None)}


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("meeting_days", "blank, placeholder, or oversized value"),
        ("start_time", "invalid historical time interval"),
        ("end_time", "invalid historical time interval"),
        ("room_code", "room is required for full-stream-eligible history"),
    ],
)
def test_full_stream_tier_requires_each_placement_column(
    bootstrap_files, field, message
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    fields.remove(field)
    _write_csv(path, fields, [{name: row[name] for name in fields} for row in rows])
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError,
        match=f"canonical_records.csv:2: invalid historical record.*{message}",
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("canonical_course_code", 20),
        ("course_title", 150),
        ("faculty_name", 150),
        ("meeting_days", 10),
        ("room_code", 20),
    ],
)
def test_loader_rejects_oversized_historical_fields(bootstrap_files, field, maximum):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = "X" * (maximum + 1)
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="blank, placeholder, or oversized value"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "field", ["canonical_course_code", "course_title", "faculty_name", "room_code"]
)
@pytest.mark.parametrize("value", ["   ", "<TBD>"])
def test_loader_rejects_blank_or_placeholder_historical_fields(
    bootstrap_files, field, value
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0][field] = value
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    with pytest.raises(
        HistoricalImportError, match="blank, placeholder, or oversized value"
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_loader_accepts_field_limits(bootstrap_files):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0].update(
        canonical_course_code="S" * 20,
        course_title="T" * 150,
        meeting_days="MTWThFSaSu",
    )
    rows[1]["room_code"] = "R" * 20
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    data = load_historical_import(dataset_dir, crosswalk)

    assert data.records[0].subject_code == "S" * 20
    assert data.records[0].subject_title == "T" * 150
    assert data.records[0].meeting_days == "MTWThFSaSu"
    assert data.records[1].room_code == "R" * 20


def test_duplicate_physical_source_row_with_another_offering_is_refused(
    bootstrap_files,
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    duplicate = rows[0].copy()
    duplicate["offering_source_row"] = "999"
    duplicate["historical_record_id"] = str(
        uuid5(
            NAMESPACE_URL,
            f"chronos:{duplicate['source_workbook_sha256']}:{duplicate['source_sheet']}:999:{duplicate['source_row']}",
        )
    )
    rows.append(duplicate)
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)
    with pytest.raises(HistoricalImportError, match="duplicate source record identity"):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "field",
    ["source_row", "offering_source_row", "historical_record_id", "academic_year"],
)
def test_cli_does_not_print_raw_invalid_cells(
    bootstrap_files, field, no_database, capsys
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    secret = "synthetic-sensitive-cell-password"
    rows[0][field] = secret
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)
    assert (
        import_history.main(
            [
                "--dataset-dir",
                str(dataset_dir),
                "--faculty-crosswalk",
                str(crosswalk),
                "--validate-only",
            ]
        )
        == 1
    )
    assert secret not in capsys.readouterr().err


def test_commit_refuses_changed_reviewed_mapping_before_connecting(
    bootstrap_files, tmp_path, no_database, capsys
):
    dataset_dir, crosswalk = bootstrap_files
    data = load_historical_import(dataset_dir, crosswalk)
    receipt = tmp_path / "review.json"
    receipt.write_text(
        json.dumps(
            {"action": "validated_read_only", "input_sha256": dict(data.input_sha256)}
        ),
        encoding="utf-8",
    )
    fields, rows = _read_csv(crosswalk)
    rows[0]["faculty_id"] = str(UUID("20000000-0000-0000-0000-000000000109"))
    _write_csv(crosswalk, fields, rows)
    assert (
        import_history.main(
            [
                "--dataset-dir",
                str(dataset_dir),
                "--faculty-crosswalk",
                str(crosswalk),
                "--commit",
                "--confirm-reviewed",
                "--review-manifest",
                str(receipt),
            ]
        )
        == 1
    )
    assert "input hashes changed" in capsys.readouterr().err


def test_commit_requires_review_receipt_before_reading_files(
    tmp_path, monkeypatch, no_database
):
    loader = Mock(side_effect=AssertionError("unreviewed inputs must not be read"))
    monkeypatch.setattr(import_history, "load_historical_import", loader)
    with pytest.raises(SystemExit):
        import_history.main(
            [
                "--dataset-dir",
                str(tmp_path),
                "--faculty-crosswalk",
                str(tmp_path / "missing.csv"),
                "--commit",
                "--confirm-reviewed",
            ]
        )
    loader.assert_not_called()


@pytest.mark.parametrize("year", ["2022-2023", "2023-2024"])
def test_room_absence_is_allowed_only_before_the_full_stream_window(
    bootstrap_files, year
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    for row in rows:
        row.update(
            academic_year=year, full_stream_eligible="0" if year == "2022-2023" else "1"
        )
    rows[0]["room_code"] = ""
    _write_csv(path, fields, rows)
    term_path = dataset_dir / "term_manifest.csv"
    term_fields, terms = _read_csv(term_path)
    for term in terms:
        term["academic_year"] = year
    _write_csv(term_path, term_fields, terms)
    refresh_manifest(dataset_dir)
    if year == "2023-2024":
        with pytest.raises(HistoricalImportError, match="room is required"):
            load_historical_import(dataset_dir, crosswalk)
    else:
        assert (
            load_historical_import(dataset_dir, crosswalk).records[0].room_code is None
        )


@pytest.mark.parametrize("count", [1, 3])
def test_loader_rejects_an_accepted_count_mismatch(bootstrap_files, count):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["accepted_record_count"] = count
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(HistoricalImportError, match="accepted record count mismatch"):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize("count", [0, -1, "2", True, None])
def test_loader_requires_a_positive_integer_accepted_count(bootstrap_files, count):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["accepted_record_count"] = count
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        HistoricalImportError, match="source_manifest.json: invalid bootstrap manifest"
    ):
        load_historical_import(dataset_dir, crosswalk)


@pytest.mark.parametrize(
    "content", ["{", "null", "[]", "{}", '{"source_workbook_sha256": "<TBD>"}']
)
def test_loader_rejects_malformed_or_incomplete_source_manifests(
    bootstrap_files, content
):
    dataset_dir, crosswalk = bootstrap_files
    (dataset_dir / "source_manifest.json").write_text(content, encoding="utf-8")

    with pytest.raises(
        HistoricalImportError, match="source_manifest.json: invalid bootstrap manifest"
    ):
        load_historical_import(dataset_dir, crosswalk)


def test_cli_requires_review_confirmation_before_loading_files(
    tmp_path: Path, monkeypatch, capsys, no_database
):
    loader = Mock(side_effect=AssertionError("unreviewed data must not be read"))
    monkeypatch.setattr(import_history, "load_historical_import", loader)

    with pytest.raises(SystemExit) as error:
        import_history.main(
            [
                "--dataset-dir",
                str(tmp_path / "missing-dataset"),
                "--faculty-crosswalk",
                str(tmp_path / "missing-crosswalk.csv"),
                "--commit",
            ]
        )

    assert error.value.code == 2
    loader.assert_not_called()
    no_database.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert "--commit requires --confirm-reviewed" in output.err


@pytest.mark.parametrize(
    "mode",
    [["--validate-only"], ["--commit", "--confirm-reviewed"]],
    ids=["validate", "commit"],
)
@pytest.mark.parametrize(
    "invalid", ["missing_file", "hash_mismatch", "invalid_mapping"]
)
def test_cli_rejects_invalid_files_before_connecting(
    bootstrap_files, mode, invalid, capsys, no_database
):
    dataset_dir, crosswalk = bootstrap_files
    receipt = crosswalk.with_name("review.json")
    receipt.write_text(
        json.dumps(
            {
                "action": "validated_read_only",
                "input_sha256": dict(
                    load_historical_import(dataset_dir, crosswalk).input_sha256
                ),
            }
        ),
        encoding="utf-8",
    )
    review_args = ["--review-manifest", str(receipt)] if "--commit" in mode else []
    if invalid == "missing_file":
        (dataset_dir / "canonical_records.csv").unlink()
        message = "FileNotFoundError"
    elif invalid == "hash_mismatch":
        path = dataset_dir / "canonical_records.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        message = "source-manifest hash mismatch"
    else:
        fields, rows = _read_csv(crosswalk)
        rows[0]["faculty_id"] = "<TBD>"
        _write_csv(crosswalk, fields, rows)
        message = "invalid or duplicate reviewed mapping"

    assert (
        import_history.main(
            [
                "--dataset-dir",
                str(dataset_dir),
                "--faculty-crosswalk",
                str(crosswalk),
                *mode,
                *review_args,
            ]
        )
        == 1
    )

    no_database.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert "historical import failed" in output.err
    assert message in output.err


def test_cli_rejects_non_postgresql_without_connecting(
    bootstrap_files, monkeypatch, capsys, no_database
):
    dataset_dir, crosswalk = bootstrap_files
    monkeypatch.setattr(
        import_history,
        "engine",
        SimpleNamespace(
            dialect=SimpleNamespace(name="sqlite"),
            connect=no_database,
        ),
    )

    assert (
        import_history.main(
            [
                "--dataset-dir",
                str(dataset_dir),
                "--faculty-crosswalk",
                str(crosswalk),
                "--validate-only",
            ]
        )
        == 1
    )

    no_database.assert_not_called()
    assert "requires local PostgreSQL 17" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode",
    [["--validate-only"], ["--commit", "--confirm-reviewed"]],
    ids=["validate", "commit"],
)
@pytest.mark.parametrize(
    "error_type", [SQLAlchemyError, OperationalError], ids=["sqlalchemy", "operational"]
)
def test_cli_does_not_expose_dsn_or_password_in_sqlalchemy_errors(
    bootstrap_files, mode, error_type, capsys, no_database
):
    dataset_dir, crosswalk = bootstrap_files
    receipt = crosswalk.with_name("review.json")
    receipt.write_text(
        json.dumps(
            {
                "action": "validated_read_only",
                "input_sha256": dict(
                    load_historical_import(dataset_dir, crosswalk).input_sha256
                ),
            }
        ),
        encoding="utf-8",
    )
    review_args = ["--review-manifest", str(receipt)] if "--commit" in mode else []
    password = "synthetic-secret-password"
    dsn = f"postgresql://synthetic-user:{password}@127.0.0.1:1/synthetic_test"
    error = (
        OperationalError("SYNTHETIC STATEMENT", {"dsn": dsn}, RuntimeError(dsn))
        if error_type is OperationalError
        else SQLAlchemyError(dsn)
    )
    no_database.side_effect = error

    assert (
        import_history.main(
            [
                "--dataset-dir",
                str(dataset_dir),
                "--faculty-crosswalk",
                str(crosswalk),
                *mode,
                *review_args,
            ]
        )
        == 1
    )

    no_database.assert_called_once_with()
    output = capsys.readouterr()
    assert output.out == ""
    assert (
        output.err
        == f"historical import failed ({error_type.__name__}); details omitted\n"
    )
    assert dsn not in output.out + output.err
    assert password not in output.out + output.err


def test_parser_collects_every_rejected_record_while_the_cli_load_fails_on_the_first(
    bootstrap_files,
):
    dataset_dir, crosswalk = bootstrap_files
    path = dataset_dir / "canonical_records.csv"
    fields, rows = _read_csv(path)
    rows[0]["meeting_days"] = "XYZ"
    rows[1]["end_time"] = "07:00"
    _write_csv(path, fields, rows)
    refresh_manifest(dataset_dir)

    data = parse_historical_import(
        {name: (dataset_dir / name).read_bytes() for name in BUNDLE_FILES},
        crosswalk.read_bytes(),
    )

    assert data.records == ()
    assert data.rejections == (
        HistoricalRowRejection("canonical_records.csv", 2, "invalid canonical meeting days"),
        HistoricalRowRejection("canonical_records.csv", 3, "invalid historical time interval"),
    )
    with pytest.raises(
        HistoricalImportError,
        match=r"canonical_records.csv:2: invalid historical record \(invalid canonical meeting days\)",
    ):
        load_historical_import(dataset_dir, crosswalk)
