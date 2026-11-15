"""CHR-108 / CHR-ING-001 pure validation of canonical historical files."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.calendar import academic_year_start
from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream


class HistoricalImportError(ValueError):
    """Invalid input or an unsafe historical import, without raw row contents."""


@dataclass(frozen=True, slots=True)
class HistoricalImportRecord:
    historical_record_id: UUID
    academic_year: str
    term_number: int
    source_faculty_id: UUID
    faculty_name: str
    subject_code: str
    subject_title: str
    room_code: str | None
    # Absent, with the room, for teacher-subject-only (2018-2022) history.
    meeting_days: str | None
    time_slot: str | None

    @property
    def period(self) -> tuple[str, int]:
        return self.academic_year, self.term_number


@dataclass(frozen=True, slots=True)
class HistoricalRowRejection:
    """One canonical record refused, located by file and CSV line only."""

    file: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: invalid historical record ({self.reason})"


@dataclass(frozen=True, slots=True)
class HistoricalImportData:
    records: tuple[HistoricalImportRecord, ...]
    empty_periods: tuple[tuple[str, int], ...]
    input_sha256: tuple[tuple[str, str], ...]
    # Accepted records are `records`; a bundle with any rejection is never
    # importable, but the full list is kept so a reviewer sees every problem.
    rejections: tuple[HistoricalRowRejection, ...] = ()

    @property
    def periods(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted({record.period for record in self.records}))


_RECORD_FIELDS = {
    "historical_record_id",
    "source_workbook_sha256",
    "source_sheet",
    "source_row",
    "offering_source_row",
    "academic_year",
    "term_number",
    "group_number",
    "canonical_course_code",
    "course_title",
    "faculty_name",
    "teacher_subject_eligible",
    "full_stream_eligible",
}
# Required only of full-stream (2023 onward) records, so a file holding only
# 2018-2022 history may omit these columns (FR-ING-002).
_PLACEMENT_FIELDS = ("meeting_days", "start_time", "end_time", "room_code")
_TERM_FIELDS = {
    "source_sheet",
    "academic_year",
    "term_number",
    "complete",
    "include_in_research",
    "exclusion_reason",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_DAYS = ("M", "T", "W", "Th", "F", "Sa", "Su")


def _rows(data: bytes, name: str, fields: set[str], *, exact: bool = False):
    try:
        source = io.StringIO(data.decode("utf-8-sig"), newline="")
        reader = csv.DictReader(source, strict=True)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not fields <= set(header):
            raise HistoricalImportError(f"{name}: invalid or missing CSV columns")
        if exact and set(header) != fields:
            raise HistoricalImportError(f"{name}: unexpected CSV columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise HistoricalImportError(
                    f"{name}:{reader.line_num}: malformed CSV row"
                )
            yield reader.line_num, row
    except (UnicodeError, csv.Error) as error:
        raise HistoricalImportError(
            f"{name}: invalid CSV encoding or syntax"
        ) from error


def _period(row: dict[str, str]) -> tuple[str, int]:
    year = row["academic_year"]
    try:
        academic_year_start(year)
    except ValueError as error:
        raise ValueError("invalid academic-year format") from error
    if row["term_number"] not in {"1", "2", "3"}:
        raise ValueError("term_number must be 1, 2, or 3")
    return year, int(row["term_number"])


def _required(value: str, maximum: int) -> str:
    if not value.strip() or "<TBD>" in value.upper() or len(value) > maximum:
        raise ValueError("blank, placeholder, or oversized value")
    return value


BUNDLE_FILES = ("canonical_records.csv", "term_manifest.csv", "source_manifest.json")


def load_historical_import(
    dataset_dir: Path, faculty_crosswalk: Path
) -> HistoricalImportData:
    """Validate the exact bytes parsed, without ORM, database, or mining access.

    The bootstrap CLI imports whole bundles only, so the first rejected record
    fails the load.
    """
    data = parse_historical_import(
        {name: (dataset_dir / name).read_bytes() for name in BUNDLE_FILES},
        faculty_crosswalk.read_bytes(),
    )
    if data.rejections:
        raise HistoricalImportError(str(data.rejections[0]))
    return data


def parse_historical_import(
    files: dict[str, bytes], crosswalk_bytes: bytes
) -> HistoricalImportData:
    """Validate a bundle, collecting every canonical-record rejection.

    Bundle-level problems (hashes, manifest, mapping, term decisions, CSV
    structure) still raise: without them no record can be judged.
    """
    try:
        manifest = json.loads(files["source_manifest.json"])
        expected = {
            "canonical_records.csv": manifest["output_sha256"]["canonical_records.csv"],
            "term_manifest.csv": manifest["input_sha256"]["term_manifest"],
        }
        workbook_hash = manifest["source_workbook_sha256"]
        count = manifest["accepted_record_count"]
        if not isinstance(workbook_hash, str) or not _SHA256.fullmatch(workbook_hash):
            raise ValueError("invalid source workbook hash")
        if type(count) is not int or count < 1:
            raise ValueError("invalid accepted record count")
        for name, expected_hash in expected.items():
            if not isinstance(expected_hash, str) or not _SHA256.fullmatch(
                expected_hash
            ):
                raise ValueError("invalid input hash commitment")
            if hashlib.sha256(files[name]).hexdigest() != expected_hash:
                raise HistoricalImportError(f"{name}: source-manifest hash mismatch")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, HistoricalImportError):
            raise
        raise HistoricalImportError(
            "source_manifest.json: invalid bootstrap manifest"
        ) from error

    faculty: dict[str, UUID] = {}
    for line, row in _rows(
        crosswalk_bytes,
        "faculty_crosswalk.csv",
        {"faculty_name", "faculty_id"},
        exact=True,
    ):
        try:
            name = " ".join(_required(row["faculty_name"], 150).split()).upper()
            faculty_id = UUID(row["faculty_id"])
            if not faculty_id.int or faculty_id == uuid5(
                NAMESPACE_URL, f"chronos:historical-faculty:{name}"
            ):
                raise ValueError("research or nil key is not a local faculty ID")
            if name in faculty:
                raise ValueError("duplicate historical faculty name")
            faculty[name] = faculty_id
        except ValueError as error:
            raise HistoricalImportError(
                f"faculty_crosswalk.csv:{line}: invalid or duplicate reviewed mapping"
            ) from error

    terms: dict[str, tuple[str, int]] = {}
    eligible_periods: set[tuple[str, int]] = set()
    sheets: set[str] = set()
    for line, row in _rows(
        files["term_manifest.csv"], "term_manifest.csv", _TERM_FIELDS, exact=True
    ):
        try:
            sheet = _required(row["source_sheet"], 200)
            if (
                sheet in sheets
                or row["complete"] not in {"true", "false"}
                or row["include_in_research"] not in {"true", "false"}
            ):
                raise ValueError("invalid or duplicate term decision")
            sheets.add(sheet)
            if row["include_in_research"] == "false":
                continue
            period = _period(row)
            if row["complete"] != "true" or period in eligible_periods:
                raise ValueError("included term must be complete and unique")
            terms[sheet] = period
            eligible_periods.add(period)
        except ValueError as error:
            raise HistoricalImportError(
                f"term_manifest.csv:{line}: invalid term decision"
            ) from error

    records: list[HistoricalImportRecord] = []
    rejections: list[HistoricalRowRejection] = []
    record_ids: set[UUID] = set()
    source_rows: set[tuple[str, int]] = set()
    # What was taught, not where it was read from: a row copied under another
    # source identity repeats this key, while two distinct Offerings sharing a
    # placement still differ by subject or group number.
    observations: set[tuple[object, ...]] = set()
    policy = HistoricalInclusionPolicy()
    for line, row in _rows(
        files["canonical_records.csv"], "canonical_records.csv", _RECORD_FIELDS
    ):
        try:
            period = _period(row)
            if terms.get(row["source_sheet"]) != period:
                raise ValueError("period is not complete and included for this sheet")
            if row["source_workbook_sha256"] != workbook_hash:
                raise ValueError("source workbook commitment differs")
            try:
                source_row, offering_row = (
                    int(row["source_row"]),
                    int(row["offering_source_row"]),
                )
            except ValueError as error:
                raise ValueError("invalid source row number") from error
            if source_row < 1 or offering_row < 1:
                raise ValueError("source row must be positive")
            try:
                record_id = UUID(row["historical_record_id"])
            except ValueError as error:
                raise ValueError("invalid source record UUID") from error
            expected_id = uuid5(
                NAMESPACE_URL,
                f"chronos:{workbook_hash}:{row['source_sheet']}:{offering_row}:{source_row}",
            )
            source_key = row["source_sheet"], source_row
            if (
                record_id != expected_id
                or record_id in record_ids
                or source_key in source_rows
            ):
                raise ValueError("invalid or duplicate source record identity")
            name = _required(row["faculty_name"], 150)
            normalized_name = " ".join(name.split()).upper()
            if normalized_name not in faculty:
                raise ValueError("missing reviewed faculty mapping")
            subject = _required(row["canonical_course_code"], 20)
            group = row["group_number"]
            if not group.isdecimal() or len(group) > 4 or int(group) < 1:
                raise ValueError("invalid group number")
            title = _required(row["course_title"], 150)
            days = time_slot = room = None
            # 2018-2022 history keeps only teacher and subject (FR-ING-003), so
            # its placement is neither required nor retained.
            if policy.is_eligible(PatternStream.TEACHER_DAY, period[0]):
                placement = {field: row.get(field, "") for field in _PLACEMENT_FIELDS}
                days = _required(placement["meeting_days"], 10)
                tokens = re.findall(r"Th|Sa|Su|M|T|W|F", days)
                if (
                    "".join(tokens) != days
                    or len(tokens) != len(set(tokens))
                    or tokens != sorted(tokens, key=_DAYS.index)
                ):
                    raise ValueError("invalid canonical meeting days")
                start, end = placement["start_time"], placement["end_time"]
                if (
                    not _CLOCK.fullmatch(start)
                    or not _CLOCK.fullmatch(end)
                    or end <= start
                ):
                    raise ValueError("invalid historical time interval")
                time_slot = f"{start}-{end}"
                if not placement["room_code"]:
                    raise ValueError(
                        "room is required for full-stream-eligible history"
                    )
                room = _required(placement["room_code"], 20)
            for field, stream in (
                ("teacher_subject_eligible", PatternStream.TEACHER_SUBJECT),
                ("full_stream_eligible", PatternStream.TEACHER_DAY),
            ):
                if row[field] not in {"0", "1"} or (
                    row[field] == "1"
                ) != policy.is_eligible(stream, period[0]):
                    raise ValueError("historical eligibility flag differs from policy")
            # Compare source meetings before the older-tier storage projection.
            observation = (
                period,
                subject,
                int(group),
                faculty[normalized_name],
                *(row.get(field, "") for field in _PLACEMENT_FIELDS),
            )
            if observation in observations:
                raise ValueError("duplicate teaching observation")
            records.append(
                HistoricalImportRecord(
                    record_id,
                    *period,
                    faculty[normalized_name],
                    name,
                    subject,
                    title,
                    room,
                    days,
                    time_slot,
                )
            )
            record_ids.add(record_id)
            source_rows.add(source_key)
            observations.add(observation)
        except ValueError as error:
            rejections.append(
                HistoricalRowRejection("canonical_records.csv", line, str(error))
            )
    if not rejections and len(records) != count:
        raise HistoricalImportError(
            "canonical_records.csv: accepted record count mismatch"
        )
    populated = {record.period for record in records}
    hashes = tuple(
        (name, hashlib.sha256(data).hexdigest()) for name, data in files.items()
    )
    hashes += (("faculty_crosswalk.csv", hashlib.sha256(crosswalk_bytes).hexdigest()),)
    return HistoricalImportData(
        tuple(records),
        tuple(sorted(eligible_periods - populated)),
        hashes,
        tuple(rejections),
    )
