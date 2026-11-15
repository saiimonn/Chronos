"""CHR-98 / CHR-COLAB-002 deterministic historical-workbook preprocessing.

The source export represents multi-meeting offerings as a primary row followed
by schedule-only continuation rows. This tool retains row provenance and never
guesses a term's research eligibility or a course's program scope.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd


REQUIRED_COLUMNS = (
    "Group Number",
    "Course Code",
    "Description",
    "Academic Units",
    "Academic Period - Year",
    "Gender",
    "Course Status",
    "Request Status",
    "Population",
    "Teacher/s",
    "Disapproved Remarks",
    "Disapproved Date",
    "Schedule",
    "Owner",
)
SCOPE_PROGRAMS = frozenset({"CS", "IT", "IS"})
OUT_OF_SCOPE_PREFIXES = ("ICT ",)
DISSOLVED_COURSE_STATUS = "DISSOLVED"
ACADEMIC_YEAR = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})$")
SOURCE_PERIOD = re.compile(
    r"^(?P<term>1ST|FIRST|2ND|SECOND|3RD|THIRD|SUMMER)\s*"
    r"(?:SEMESTER|TERM)?\s*-?\s*(?:AY\s*)?(?P<start_year>\d{4})"
    r"(?:-(?P<end_year>\d{2}|\d{4}))?$",
    re.IGNORECASE,
)
SCHEDULE = re.compile(
    r"^(?P<days>[A-Za-z]+)\s+(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
    r"(?P<end>\d{1,2}:\d{2}\s*[AP]M)\s+(?P<room>.+)$",
    re.IGNORECASE,
)
DAY_TOKENS = (("TH", "Th"), ("SAT", "Sa"), ("SA", "Sa"), ("SUN", "Su"), ("SU", "Su"), ("M", "M"), ("T", "T"), ("W", "W"), ("F", "F"))
DAY_ORDER = ("M", "T", "W", "Th", "F", "Sa", "Su")
RECORD_FIELDNAMES = (
    "historical_record_id",
    "source_workbook_sha256",
    "source_sheet",
    "source_row",
    "offering_source_row",
    "academic_year",
    "term_number",
    "in_scope_programs",
    "group_number",
    "source_course_code",
    "course_code",
    "canonical_course_code",
    "equivalent_course_code",
    "course_title",
    "academic_units",
    "faculty_name",
    "meeting_days",
    "start_time",
    "end_time",
    "room_code",
    "original_schedule",
    "teacher_subject_eligible",
    "full_stream_eligible",
)
OUTPUT_FILENAMES = frozenset(
    {
        "canonical_records.csv",
        "training_records.csv",
        "holdout_records.csv",
        "rejection_ledger.csv",
        "source_manifest.json",
    }
)


@dataclass(frozen=True, slots=True)
class TermManifestRow:
    source_sheet: str
    academic_year: str | None
    term_number: int | None
    complete: bool
    include_in_research: bool
    exclusion_reason: str


@dataclass(frozen=True, slots=True)
class LedgerRow:
    source_sheet: str
    source_row: int
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class CrosswalkRow:
    canonical_course_code: str
    programs: tuple[str, ...]
    source_course_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CodeCrosswalkRow:
    equivalent_course_code: str
    programs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourcePeriod:
    term_number: int
    academic_year: str | None
    source_year: int


def normalized(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).replace("\u00a0", " ").split()).strip()


def truthy(value: str) -> bool:
    value = normalized(value).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"expected true or false, got {value!r}")
    return value == "true"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_term_manifest(path: Path) -> dict[str, TermManifestRow]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    required = {
        "source_sheet",
        "academic_year",
        "term_number",
        "complete",
        "include_in_research",
        "exclusion_reason",
    }
    if not rows or set(rows[0]) != required:
        raise ValueError(f"{path} must have exactly these columns: {sorted(required)}")

    manifest: dict[str, TermManifestRow] = {}
    for row in rows:
        source_sheet = normalized(row["source_sheet"])
        academic_year = normalized(row["academic_year"])
        if not source_sheet or source_sheet in manifest:
            raise ValueError(f"source_sheet must be unique and non-blank: {source_sheet!r}")
        complete = truthy(row["complete"])
        included = truthy(row["include_in_research"])
        exclusion_reason = normalized(row["exclusion_reason"])
        if included and not complete:
            raise ValueError(f"incomplete term cannot enter research: {source_sheet!r}")
        if included and exclusion_reason:
            raise ValueError(f"included term cannot have an exclusion reason: {source_sheet!r}")
        if not included and not academic_year and not normalized(row["term_number"]):
            if not exclusion_reason:
                raise ValueError(f"excluded non-term sheet needs a reason: {source_sheet!r}")
            term_number = None
        else:
            match = ACADEMIC_YEAR.fullmatch(academic_year)
            if not match or int(match["end"]) != int(match["start"]) + 1:
                raise ValueError(
                    f"invalid academic year for {source_sheet!r}: {academic_year!r}"
                )
            term_number = int(normalized(row["term_number"]))
            if term_number not in {1, 2, 3}:
                raise ValueError(f"term_number must be 1, 2, or 3 for {source_sheet!r}")
        if not included and not exclusion_reason:
            raise ValueError(f"excluded sheet needs a reason: {source_sheet!r}")
        manifest[source_sheet] = TermManifestRow(
            source_sheet,
            academic_year or None,
            term_number,
            complete,
            included,
            exclusion_reason,
        )
    return manifest


def load_scope_mapping(path: Path) -> dict[str, tuple[str, ...]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows or set(rows[0]) != {"course_code", "programs"}:
        raise ValueError(f"{path} must have course_code,programs columns")
    mapping: dict[str, tuple[str, ...]] = {}
    for row in rows:
        course_code = normalized(row["course_code"]).upper()
        programs = tuple(
            sorted(
                {normalized(value).upper() for value in row["programs"].split("|")},
            )
        )
        if (
            not course_code
            or not programs
            or any(program not in SCOPE_PROGRAMS for program in programs)
            or course_code in mapping
        ):
            raise ValueError(f"invalid or duplicate scope mapping: {row!r}")
        mapping[course_code] = programs
    return mapping


def load_title_crosswalk(path: Path | None) -> dict[str, CrosswalkRow]:
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    required = {"source_title", "canonical_course_code", "programs"}
    headers = set(rows[0]) if rows else set()
    if headers != required and headers != required | {"source_course_codes"}:
        raise ValueError(f"{path} must have source_title,canonical_course_code,programs[,source_course_codes] columns")
    mapping: dict[str, CrosswalkRow] = {}
    for row in rows:
        source_title = normalized(row["source_title"]).upper()
        canonical_course_code = normalized(row["canonical_course_code"]).upper()
        programs = tuple(sorted({normalized(value).upper() for value in row["programs"].split("|")}))
        source_course_codes = frozenset(
            normalized(value).upper()
            for value in row.get("source_course_codes", "").split("|")
            if normalized(value)
        )
        if (
            not source_title
            or not canonical_course_code
            or not programs
            or any(program not in SCOPE_PROGRAMS for program in programs)
            or source_title in mapping
        ):
            raise ValueError(f"invalid or duplicate title crosswalk: {row!r}")
        mapping[source_title] = CrosswalkRow(
            canonical_course_code, programs, source_course_codes
        )
    return mapping


def load_code_crosswalk(path: Path | None) -> dict[str, CodeCrosswalkRow]:
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows or set(rows[0]) != {"source_course_code", "equivalent_course_code", "programs"}:
        raise ValueError(f"{path} must have source_course_code,equivalent_course_code,programs columns")
    mapping: dict[str, CodeCrosswalkRow] = {}
    for row in rows:
        source_course_code = normalized(row["source_course_code"]).upper()
        equivalent_course_code = normalized(row["equivalent_course_code"]).upper()
        programs = tuple(sorted({normalized(value).upper() for value in row["programs"].split("|")}))
        if (
            not source_course_code
            or not equivalent_course_code
            or not programs
            or any(program not in SCOPE_PROGRAMS for program in programs)
            or source_course_code in mapping
        ):
            raise ValueError(f"invalid or duplicate code crosswalk: {row!r}")
        mapping[source_course_code] = CodeCrosswalkRow(equivalent_course_code, programs)
    return mapping


def load_excluded_course_codes(path: Path | None) -> frozenset[str]:
    if path is None:
        return frozenset()
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows or set(rows[0]) != {"course_code"}:
        raise ValueError(f"{path} must have a course_code column")
    course_codes = [normalized(row["course_code"]).upper() for row in rows]
    if any(not course_code for course_code in course_codes) or len(set(course_codes)) != len(course_codes):
        raise ValueError(f"invalid or duplicate excluded course code in {path}")
    return frozenset(course_codes)


def canonical_days(value: str) -> str:
    value = value.upper()
    days: list[str] = []
    offset = 0
    while offset < len(value):
        match = next(
            ((source, canonical) for source, canonical in DAY_TOKENS if value.startswith(source, offset)),
            None,
        )
        if match is None or match[1] in days:
            raise ValueError(f"invalid day pattern: {value!r}")
        source, token = match
        days.append(token)
        offset += len(source)
    return "".join(sorted(days, key=DAY_ORDER.index))


def canonical_time(value: str) -> str:
    return datetime.strptime(value.upper().replace(" ", ""), "%I:%M%p").strftime("%H:%M")


def parse_source_period(value: str) -> SourcePeriod:
    match = SOURCE_PERIOD.fullmatch(normalized(value))
    if match is None:
        raise ValueError("academic period does not identify a supported term and year")
    term = match["term"].upper()
    term_number = 1 if term in {"1ST", "FIRST"} else 2 if term in {"2ND", "SECOND"} else 3
    start_year = int(match["start_year"])
    end_year = match["end_year"]
    if end_year is None:
        return SourcePeriod(term_number, None, start_year)
    normalized_end_year = (
        int(str(start_year)[:2] + end_year) if len(end_year) == 2 else int(end_year)
    )
    if normalized_end_year != start_year + 1:
        raise ValueError("academic period must span consecutive years")
    return SourcePeriod(term_number, f"{start_year}-{normalized_end_year}", start_year)


def parse_schedule(value: str) -> tuple[str, str, str, str]:
    match = SCHEDULE.fullmatch(normalized(value))
    if match is None:
        raise ValueError("schedule does not match '<days> <start> - <end> <room>'")
    start, end = canonical_time(match["start"]), canonical_time(match["end"])
    if end <= start:
        raise ValueError("schedule ends before or at its start")
    room = normalized(match["room"]).upper()
    if not room:
        raise ValueError("schedule room is blank")
    return canonical_days(match["days"]), start, end, room


def primary_values(row: dict[str, object]) -> bool:
    return any(normalized(row[column]) for column in REQUIRED_COLUMNS if column != "Schedule")


def source_record_id(workbook_hash: str, sheet: str, row: int, meeting: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"chronos:{workbook_hash}:{sheet}:{row}:{meeting}"))


def process_workbook(
    workbook: Path,
    manifest: dict[str, TermManifestRow],
    scope_mapping: dict[str, tuple[str, ...]],
    title_crosswalk: dict[str, CrosswalkRow] | None = None,
    code_crosswalk: dict[str, CodeCrosswalkRow] | None = None,
    excluded_course_codes: frozenset[str] | None = None,
) -> tuple[list[dict[str, object]], list[LedgerRow], dict[str, object]]:
    workbook_hash = sha256(workbook)
    sheets = pd.read_excel(workbook, sheet_name=None, dtype=str, engine="openpyxl")
    non_empty_sheets = {name for name, frame in sheets.items() if not frame.dropna(how="all").empty}
    missing_manifest = non_empty_sheets - set(manifest)
    unknown_sheet = set(manifest) - set(sheets)
    if missing_manifest or unknown_sheet:
        raise ValueError(
            "term manifest must account for every non-empty sheet; "
            f"missing={sorted(missing_manifest)}, unknown={sorted(unknown_sheet)}"
        )

    accepted: list[dict[str, object]] = []
    ledger: list[LedgerRow] = []
    title_crosswalk = title_crosswalk or {}
    code_crosswalk = code_crosswalk or {}
    excluded_course_codes = excluded_course_codes or frozenset()
    for sheet_name, frame in sheets.items():
        if sheet_name not in manifest:
            continue
        term = manifest[sheet_name]
        if not term.include_in_research:
            continue
        missing_columns = set(REQUIRED_COLUMNS) - set(frame.columns)
        if missing_columns:
            raise ValueError(f"{sheet_name!r} is missing columns: {sorted(missing_columns)}")
        parent: tuple[int, dict[str, object]] | None = None
        for source_row, (_, row) in enumerate(frame.iterrows(), start=2):
            values = {column: normalized(row[column]) for column in REQUIRED_COLUMNS}
            course_code = values["Course Code"].upper()
            schedule = values["Schedule"]
            if not course_code:
                if not schedule:
                    if primary_values(values):
                        ledger.append(LedgerRow(sheet_name, source_row, "MISSING_COURSE_CODE", "primary row has no course code"))
                    parent = None
                    continue
                if primary_values(values) or parent is None:
                    ledger.append(LedgerRow(sheet_name, source_row, "ORPHAN_CONTINUATION", "schedule row has no preceding offering"))
                    parent = None
                    continue
                parent_row, parent_values = parent
                values = parent_values | {"Schedule": schedule}
                offering_row = parent_row
                course_code = values["Course Code"].upper()
            else:
                parent = (source_row, values)
                offering_row = source_row

            try:
                source_period = parse_source_period(values["Academic Period - Year"])
            except ValueError as exc:
                ledger.append(LedgerRow(sheet_name, source_row, "UNPARSEABLE_SOURCE_PERIOD", str(exc)))
                parent = None
                continue
            term_start_year, term_end_year = map(int, str(term.academic_year).split("-"))
            source_year_matches_term = source_period.source_year in {
                term_start_year,
                term_end_year,
            }
            if source_period.term_number != term.term_number or (
                source_period.academic_year is not None
                and source_period.academic_year != term.academic_year
            ) or not source_year_matches_term:
                ledger.append(
                    LedgerRow(
                        sheet_name,
                        source_row,
                        "SOURCE_PERIOD_MANIFEST_MISMATCH",
                        f"source={values['Academic Period - Year']!r}; "
                        f"manifest={term.academic_year} term {term.term_number}",
                    )
                )
                parent = None
                continue

            if values["Course Status"].upper() == DISSOLVED_COURSE_STATUS:
                ledger.append(LedgerRow(sheet_name, source_row, "DISSOLVED_COURSE", course_code))
                continue
            source_course_code = course_code
            if course_code.startswith(OUT_OF_SCOPE_PREFIXES) or course_code in excluded_course_codes:
                ledger.append(LedgerRow(sheet_name, source_row, "OUT_OF_SCOPE_COURSE", course_code))
                continue
            code_crosswalk_row = code_crosswalk.get(course_code)
            crosswalk = title_crosswalk.get(values["Description"].upper())
            if crosswalk is not None and (
                crosswalk.source_course_codes
                and source_course_code not in crosswalk.source_course_codes
            ):
                crosswalk = None
            if code_crosswalk_row is not None:
                programs = code_crosswalk_row.programs
                equivalent_course_code = code_crosswalk_row.equivalent_course_code
            elif crosswalk is not None:
                course_code, programs = crosswalk.canonical_course_code, crosswalk.programs
                equivalent_course_code = course_code
            else:
                programs = scope_mapping.get(course_code)
                if programs is None:
                    ledger.append(LedgerRow(sheet_name, source_row, "UNMAPPED_COURSE_SCOPE", course_code))
                    continue
                equivalent_course_code = ""
            canonical_course_code = equivalent_course_code or course_code
            if not values["Description"]:
                ledger.append(LedgerRow(sheet_name, source_row, "MISSING_SUBJECT_DETAILS", source_course_code))
                continue
            faculty = values["Teacher/s"].upper()
            if not faculty:
                ledger.append(LedgerRow(sheet_name, source_row, "MISSING_FACULTY", course_code))
                continue
            if not schedule:
                ledger.append(LedgerRow(sheet_name, source_row, "MISSING_SCHEDULE", course_code))
                continue
            try:
                days, start, end, room = parse_schedule(schedule)
            except ValueError as exc:
                ledger.append(LedgerRow(sheet_name, source_row, "UNPARSEABLE_SCHEDULE", str(exc)))
                continue
            accepted.append(
                {
                    "historical_record_id": source_record_id(workbook_hash, sheet_name, offering_row, source_row),
                    "source_workbook_sha256": workbook_hash,
                    "source_sheet": sheet_name,
                    "source_row": source_row,
                    "offering_source_row": offering_row,
                    "academic_year": term.academic_year,
                    "term_number": term.term_number,
                    "in_scope_programs": "|".join(programs),
                    "group_number": values["Group Number"],
                    "source_course_code": source_course_code,
                    "course_code": course_code,
                    "canonical_course_code": canonical_course_code,
                    "equivalent_course_code": equivalent_course_code,
                    "course_title": values["Description"].upper(),
                    "academic_units": values["Academic Units"],
                    "faculty_name": faculty,
                    "meeting_days": days,
                    "start_time": start,
                    "end_time": end,
                    "room_code": room,
                    "original_schedule": schedule,
                    "teacher_subject_eligible": int(
                        int(term.academic_year[:4]) >= 2018
                    ),
                    "full_stream_eligible": int(
                        int(term.academic_year[:4]) >= 2023
                    ),
                }
            )

    accepted.sort(
        key=lambda item: (
            item["academic_year"],
            item["term_number"],
            item["source_sheet"],
            item["offering_source_row"],
            item["source_row"],
        )
    )
    duplicates: set[tuple[object, ...]] = set()
    deduplicated: list[dict[str, object]] = []
    for record in accepted:
        key = tuple(
            record[name]
            for name in (
                "academic_year",
                "term_number",
                "canonical_course_code",
                "group_number",
                "faculty_name",
                "meeting_days",
                "start_time",
                "end_time",
                "room_code",
            )
        )
        if key in duplicates:
            ledger.append(LedgerRow(str(record["source_sheet"]), int(record["source_row"]), "LOGICAL_DUPLICATE", repr(key)))
            continue
        duplicates.add(key)
        deduplicated.append(record)

    details = {
        "source_workbook": workbook.name,
        "source_workbook_sha256": workbook_hash,
        "source_sheets": sorted(sheets),
        "selected_terms": [
            asdict(item)
            for item in sorted(
                manifest.values(),
                key=lambda item: (
                    item.academic_year or "",
                    item.term_number or 0,
                    item.source_sheet,
                ),
            )
        ],
        "accepted_record_count": len(deduplicated),
        "ledger_counts": dict(sorted(Counter(item.reason_code for item in ledger).items())),
    }
    return deduplicated, ledger, details


def split_holdout(
    records: list[dict[str, object]], manifest: dict[str, TermManifestRow] | None = None
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[tuple[str, int]]]:
    periods = (
        sorted(
            {(item.academic_year, item.term_number) for item in manifest.values() if item.include_in_research}
        )
        if manifest is not None
        else sorted({(str(item["academic_year"]), int(item["term_number"])) for item in records})
    )
    if len(periods) < 4:
        raise ValueError("at least four complete, research-eligible terms are required for a three-term holdout")
    holdout_periods = periods[-3:]
    training = [item for item in records if (item["academic_year"], item["term_number"]) not in holdout_periods]
    holdout = [item for item in records if (item["academic_year"], item["term_number"]) in holdout_periods]
    if manifest is not None and any(
        not any((item["academic_year"], item["term_number"]) == period for item in records)
        for period in holdout_periods
    ):
        raise ValueError("each chronological holdout term must contain accepted records")
    return training, holdout, holdout_periods


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = list(rows[0]) if rows else (
        RECORD_FIELDNAMES if path.name != "rejection_ledger.csv" else [
            "source_sheet",
            "source_row",
            "reason_code",
            "detail",
        ]
    )
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def replace_output_directory(staging_dir: Path, output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output path must be a directory: {output_dir}")
    unknown_entries = set(output_dir.iterdir()) - {
        output_dir / name for name in OUTPUT_FILENAMES
    } if output_dir.exists() else set()
    if unknown_entries:
        raise ValueError(
            f"output directory contains files not managed by this tool: "
            f"{sorted(path.name for path in unknown_entries)}"
        )
    previous_dir = output_dir.with_name(f".{output_dir.name}-previous-{uuid4().hex}")
    if output_dir.exists():
        os.replace(output_dir, previous_dir)
    try:
        os.replace(staging_dir, output_dir)
    except OSError:
        if previous_dir.exists():
            os.replace(previous_dir, output_dir)
        raise
    if previous_dir.exists():
        shutil.rmtree(previous_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--term-manifest", type=Path, required=True)
    parser.add_argument("--scope-mapping", type=Path, required=True)
    parser.add_argument("--title-crosswalk", type=Path)
    parser.add_argument("--code-crosswalk", type=Path)
    parser.add_argument("--excluded-course-codes", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_term_manifest(args.term_manifest)
    records, ledger, details = process_workbook(
        args.workbook,
        manifest,
        load_scope_mapping(args.scope_mapping),
        load_title_crosswalk(args.title_crosswalk),
        load_code_crosswalk(args.code_crosswalk),
        load_excluded_course_codes(args.excluded_course_codes),
    )
    training, holdout, holdout_periods = split_holdout(records, manifest)
    details["holdout_periods"] = holdout_periods
    details["training_record_count"] = len(training)
    details["holdout_record_count"] = len(holdout)
    details["input_sha256"] = {
        "workbook": sha256(args.workbook),
        "term_manifest": sha256(args.term_manifest),
        "scope_mapping": sha256(args.scope_mapping),
        "title_crosswalk": sha256(args.title_crosswalk) if args.title_crosswalk else None,
        "code_crosswalk": sha256(args.code_crosswalk) if args.code_crosswalk else None,
        "excluded_course_codes": sha256(args.excluded_course_codes)
        if args.excluded_course_codes
        else None,
        "preprocessor": sha256(Path(__file__)),
    }
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output_dir.parent, prefix=f".{args.output_dir.name}-") as temporary:
        staging_dir = Path(temporary)
        write_csv(staging_dir / "canonical_records.csv", records)
        write_csv(staging_dir / "training_records.csv", training)
        write_csv(staging_dir / "holdout_records.csv", holdout)
        write_csv(staging_dir / "rejection_ledger.csv", [asdict(item) for item in ledger])
        (staging_dir / "source_manifest.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
        details["output_sha256"] = {
            path.name: sha256(path)
            for path in sorted(staging_dir.iterdir())
            if path.name != "source_manifest.json"
        }
        (staging_dir / "source_manifest.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
        replace_output_directory(staging_dir, args.output_dir)


if __name__ == "__main__":
    main()
