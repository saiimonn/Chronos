"""Turn an uploaded Offered Courses workbook into a canonical historical bundle.

The administrator uploads the registrar's "Offered Courses" export, one sheet
per term ("S1 AY2025-26", "S2 AY2025-26", "Summer 2026"). The decisions that the
offline CHR-98 preprocessing took from reviewed files are taken from the local
database instead:

- a sheet is imported only when a Term for its period exists, is not the
  active Term, and was not captured before;
- a course is in scope when its code is in the Subject catalog;
- a teacher is the Faculty row whose id the roster import derived from the
  workbook name, else the one roster row whose name reads the same.

Rows that are not history to import (dissolved or out-of-scope courses, blank
or repeated header rows, offerings with no teacher or no schedule, rows
labelled with another term) are *skipped* and listed, and never block. A row
that should be imported but cannot be (teacher not on the roster, unreadable
schedule, invalid group) is *blocking*: the batch cannot be confirmed until the
workbook or roster is corrected, so no history is silently dropped.

The result is the same four-file bundle the CHR-108 parser validates, so
staging, review, and confirmation are unchanged. The offline research
preprocessor (`scripts/colab_prototype`) is hash-bound and deliberately left
untouched; the row reconstruction here mirrors it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Faculty, HistoricalSnapshot, Subject, Term
from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream
from app.services.ingestion.faculty_names import (
    _name_key,
    display_name,
    historical_faculty_id,
)
from app.services.ingestion.historical_file import HistoricalImportError

WORKBOOK_LABEL = "Offered Courses.xlsx"
TEMPLATE_FILENAME = "Offered Courses template.xlsx"
# A sheet in the downloadable template that explains the format.
INSTRUCTIONS_SHEET = "Instructions"
CONVERTER_VERSION = "offered-courses-upload/1"

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
RECORD_FIELDNAMES = (
    "historical_record_id",
    "source_workbook_sha256",
    "source_sheet",
    "source_row",
    "offering_source_row",
    "academic_year",
    "term_number",
    "group_number",
    "source_course_code",
    "canonical_course_code",
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
_SEMESTER_SHEET = re.compile(
    r"^S(?P<term>[12])\s*AY\s*(?P<start>\d{4})\s*-\s*(?P<end>\d{2}|\d{4})$", re.I
)
_SUMMER_SHEET = re.compile(r"^SUMMER\s*(?P<year>\d{4})$", re.I)
_SOURCE_PERIOD = re.compile(
    r"^(?P<term>1ST|FIRST|2ND|SECOND|3RD|THIRD|SUMMER)\s*"
    r"(?:SEMESTER|TERM)?\s*-?\s*(?:AY\s*)?(?P<start_year>\d{4})"
    r"(?:-(?P<end_year>\d{2}|\d{4}))?$",
    re.I,
)
_SCHEDULE = re.compile(
    r"^(?P<days>[A-Za-z]+)\s+(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
    r"(?P<end>\d{1,2}:\d{2}\s*[AP]M)\s+(?P<room>.+)$",
    re.I,
)
_DAY_TOKENS = (
    ("TH", "Th"),
    ("SAT", "Sa"),
    ("SA", "Sa"),
    ("SUN", "Su"),
    ("SU", "Su"),
    ("M", "M"),
    ("T", "T"),
    ("W", "W"),
    ("F", "F"),
)
_DAY_ORDER = ("M", "T", "W", "Th", "F", "Sa", "Su")
_DISSOLVED = "DISSOLVED"


@dataclass(frozen=True, slots=True)
class WorkbookIssue:
    """One sheet or row, located by sheet name and spreadsheet row number."""

    sheet: str
    row: int | None
    reason: str


@dataclass(slots=True)
class OfferedCoursesBundle:
    files: dict[str, bytes]
    workbook_sha256: str
    accepted_count: int
    skipped: list[WorkbookIssue] = field(default_factory=list)
    blocking: list[WorkbookIssue] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Catalog:
    terms: dict[tuple[str, int], Term]
    captured: set[tuple[str, int]]
    subjects: dict[str, str]
    faculty_ids: set[UUID]
    faculty_by_key: dict[str, set[UUID]]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).replace(" ", " ").split())


def _sheet_period(name: str) -> tuple[str, int] | None:
    if match := _SEMESTER_SHEET.fullmatch(name.strip()):
        start = int(match["start"])
        end = match["end"]
        end_year = int(str(start)[:2] + end) if len(end) == 2 else int(end)
        if end_year != start + 1:
            return None
        return f"{start}-{end_year}", int(match["term"])
    if match := _SUMMER_SHEET.fullmatch(name.strip()):
        year = int(match["year"])
        return f"{year - 1}-{year}", 3
    return None


def _source_period_matches(value: str, period: tuple[str, int]) -> bool:
    """The row's own "Academic Period - Year" agrees with its sheet's term."""
    match = _SOURCE_PERIOD.fullmatch(value)
    if match is None:
        return False
    term = match["term"].upper()
    number = 1 if term in {"1ST", "FIRST"} else 2 if term in {"2ND", "SECOND"} else 3
    start, end = map(int, period[0].split("-"))
    year = int(match["start_year"])
    if match["end_year"] is not None:
        tail = match["end_year"]
        end_year = int(str(year)[:2] + tail) if len(tail) == 2 else int(tail)
        if (year, end_year) != (start, end):
            return False
    return number == period[1] and year in {start, end}


def _days(value: str) -> str:
    value = value.upper()
    days: list[str] = []
    offset = 0
    while offset < len(value):
        match = next(
            (pair for pair in _DAY_TOKENS if value.startswith(pair[0], offset)), None
        )
        if match is None or match[1] in days:
            raise ValueError(f"invalid meeting days {value!r}")
        days.append(match[1])
        offset += len(match[0])
    return "".join(sorted(days, key=_DAY_ORDER.index))


def _clock(value: str) -> str:
    return datetime.strptime(value.upper().replace(" ", ""), "%I:%M%p").strftime(
        "%H:%M"
    )


def _schedule(value: str) -> tuple[str, str, str, str]:
    match = _SCHEDULE.fullmatch(value)
    if match is None:
        raise ValueError(
            f"schedule {value!r} is not '<days> <start> - <end> <room>', "
            "e.g. 'MW 09:00 AM - 10:30 AM LB447TC'"
        )
    days = _days(match["days"])
    start, end = _clock(match["start"]), _clock(match["end"])
    if end <= start:
        raise ValueError(f"schedule {value!r} ends before it starts")
    room = match["room"].strip().upper()
    if len(days) > 10 or len(room) > 20:
        raise ValueError(f"schedule {value!r} has an oversized day or room code")
    return days, start, end, room


def _load_catalog(session: Session) -> _Catalog:
    terms = {
        (term.academic_year, term.term_number): term
        for term in session.scalars(select(Term))
    }
    captured = set(
        session.execute(
            select(HistoricalSnapshot.academic_year, HistoricalSnapshot.term_number)
        ).tuples()
    )
    subjects = {
        code.upper(): title
        for code, title in session.execute(
            select(Subject.subject_code, Subject.subject_title)
        ).tuples()
    }
    faculty_ids: set[UUID] = set()
    faculty_by_key: dict[str, set[UUID]] = defaultdict(set)
    for faculty_id, full_name in session.execute(
        select(Faculty.faculty_id, Faculty.full_name)
    ).tuples():
        faculty_ids.add(faculty_id)
        faculty_by_key[_name_key(full_name)].add(faculty_id)
    return _Catalog(terms, captured, subjects, faculty_ids, dict(faculty_by_key))


def _match_faculty(catalog: _Catalog, name: str) -> UUID:
    generated = historical_faculty_id(name)
    if generated in catalog.faculty_ids:
        return generated
    found: set[UUID] = set()
    for key in {_name_key(display_name(name)), _name_key(name)}:
        found |= catalog.faculty_by_key.get(key, set())
    if len(found) == 1:
        return next(iter(found))
    if found:
        raise ValueError(
            f"teacher {name!r} matches more than one Faculty record; "
            "make the roster names distinct"
        )
    raise ValueError(
        f"no Faculty record matches teacher {name!r}; add or rename them on the Roster"
    )


def _read_workbook(data: bytes):
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as error:
        raise HistoricalImportError(
            f"{WORKBOOK_LABEL}: the file is not a readable .xlsx workbook"
        ) from error


def _csv(fieldnames: tuple[str, ...] | list[str], rows: list[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def convert_offered_courses(session: Session, data: bytes) -> OfferedCoursesBundle:
    """Build the canonical bundle; raise only when the file is not this format."""
    workbook_hash = hashlib.sha256(data).hexdigest()
    workbook = _read_workbook(data)
    catalog = _load_catalog(session)
    policy = HistoricalInclusionPolicy()
    skipped: list[WorkbookIssue] = []
    blocking: list[WorkbookIssue] = []
    records: list[dict[str, object]] = []
    crosswalk: dict[str, UUID] = {}
    term_rows: list[dict[str, str]] = []
    seen_periods: dict[tuple[str, int], str] = {}
    observations: dict[tuple[object, ...], int] = {}
    recognized_sheets = 0

    try:
        sheets = [(sheet.title, list(sheet.iter_rows(values_only=True))) for sheet in workbook.worksheets]
    finally:
        workbook.close()

    for sheet, rows in sheets:
        def exclude(reason: str, *, report: bool = True) -> None:
            term_rows.append(
                {
                    "source_sheet": sheet,
                    "academic_year": "",
                    "term_number": "",
                    "complete": "false",
                    "include_in_research": "false",
                    "exclusion_reason": reason,
                }
            )
            if report:
                skipped.append(WorkbookIssue(sheet, None, reason))

        header = [_text(value) for value in rows[0]] if rows else []
        has_data = any(_text(value) for row in rows[1:] for value in row)
        has_header = set(REQUIRED_COLUMNS) <= set(header)
        recognized_sheets += has_header
        if sheet == INSTRUCTIONS_SHEET or not has_data:
            exclude("instructions or empty sheet", report=False)
            continue
        period = _sheet_period(sheet)
        if period is None:
            exclude(
                "sheet name is not a term such as 'S1 AY2025-26', "
                "'S2 AY2025-26', or 'Summer 2026'"
            )
            continue
        label = f"{period[0]} Term {period[1]}"
        if not has_header:
            missing = [name for name in REQUIRED_COLUMNS if name not in header]
            exclude(f"sheet is missing columns: {', '.join(missing)}", report=False)
            blocking.append(
                WorkbookIssue(sheet, 1, f"missing columns: {', '.join(missing)}")
            )
            continue
        if period in seen_periods:
            exclude(f"same term as sheet {seen_periods[period]!r}", report=False)
            blocking.append(
                WorkbookIssue(sheet, None, f"{label} also appears as sheet {seen_periods[period]!r}")
            )
            continue
        seen_periods[period] = sheet
        term = catalog.terms.get(period)
        if term is None:
            exclude(f"no Term exists for {label}; create it on Term Setup to import this sheet")
            continue
        if term.is_active:
            exclude(f"{label} is the active term, not history")
            continue
        if period in catalog.captured:
            exclude(f"{label} history was already imported")
            continue
        term_rows.append(
            {
                "source_sheet": sheet,
                "academic_year": period[0],
                "term_number": str(period[1]),
                "complete": "true",
                "include_in_research": "true",
                "exclusion_reason": "",
            }
        )

        column = {name: header.index(name) for name in REQUIRED_COLUMNS}
        parent: tuple[int, dict[str, str]] | None = None
        for row_number, raw in enumerate(rows[1:], start=2):
            values = {
                name: _text(raw[index]) if index < len(raw) else ""
                for name, index in column.items()
            }

            def skip(reason: str) -> None:
                skipped.append(WorkbookIssue(sheet, row_number, reason))

            if values["Academic Period - Year"] == "Academic Period - Year":
                skip("repeated header row")
                parent = None
                continue
            primary = any(value for name, value in values.items() if name != "Schedule")
            schedule = values["Schedule"]
            if not values["Course Code"]:
                if not primary:
                    if schedule and parent is not None:
                        offering_row, values = parent[0], parent[1] | {"Schedule": schedule}
                    else:
                        if schedule:
                            skip("schedule row with no offering above it")
                        parent = None
                        continue
                else:
                    skip("row has no course code")
                    parent = None
                    continue
            else:
                parent = (row_number, values)
                offering_row = row_number

            if not _source_period_matches(values["Academic Period - Year"], period):
                skip(
                    f"labelled {values['Academic Period - Year'] or 'with no period'!r}, "
                    f"not this sheet's {label}"
                )
                continue
            if values["Course Status"].upper() == _DISSOLVED:
                skip("dissolved course")
                continue
            source_code = values["Course Code"].upper()
            title = catalog.subjects.get(source_code)
            if title is None:
                skip(f"course {source_code} is not in the Subject catalog")
                continue
            teacher = values["Teacher/s"].upper()
            if not teacher:
                skip("no teacher assigned")
                continue
            if not schedule:
                skip("no schedule")
                continue
            try:
                group = values["Group Number"]
                if not group.isdecimal() or len(group) > 4 or int(group) < 1:
                    raise ValueError(f"invalid group number {group!r}")
                days, start, end, room = _schedule(schedule)
                faculty_id = _match_faculty(catalog, teacher)
            except ValueError as error:
                blocking.append(WorkbookIssue(sheet, row_number, str(error)))
                continue
            observation = (period, source_code, int(group), faculty_id, days, start, end, room)
            if observation in observations:
                skip(f"duplicate of row {observations[observation]}")
                continue
            observations[observation] = row_number
            crosswalk[teacher] = faculty_id
            year = period[0]
            records.append(
                {
                    "historical_record_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"chronos:{workbook_hash}:{sheet}:{offering_row}:{row_number}",
                        )
                    ),
                    "source_workbook_sha256": workbook_hash,
                    "source_sheet": sheet,
                    "source_row": row_number,
                    "offering_source_row": offering_row,
                    "academic_year": year,
                    "term_number": period[1],
                    "group_number": group,
                    "source_course_code": source_code,
                    "canonical_course_code": source_code,
                    "course_title": (values["Description"] or title).upper()[:150],
                    "academic_units": values["Academic Units"],
                    "faculty_name": teacher,
                    "meeting_days": days,
                    "start_time": start,
                    "end_time": end,
                    "room_code": room,
                    "original_schedule": schedule,
                    "teacher_subject_eligible": int(
                        policy.is_eligible(PatternStream.TEACHER_SUBJECT, year)
                    ),
                    "full_stream_eligible": int(
                        policy.is_eligible(PatternStream.TEACHER_DAY, year)
                    ),
                }
            )

    if not recognized_sheets:
        raise HistoricalImportError(
            f"{WORKBOOK_LABEL}: no sheet has the Offered Courses columns; "
            "download the template for the expected format"
        )

    files = {
        "canonical_records.csv": _csv(RECORD_FIELDNAMES, records),
        "term_manifest.csv": _csv(
            (
                "source_sheet",
                "academic_year",
                "term_number",
                "complete",
                "include_in_research",
                "exclusion_reason",
            ),
            term_rows,
        ),
        "faculty_crosswalk.csv": _csv(
            ("faculty_name", "faculty_id"),
            [
                {"faculty_name": name, "faculty_id": str(faculty_id)}
                for name, faculty_id in sorted(crosswalk.items())
            ],
        ),
    }
    manifest = {
        "converter": CONVERTER_VERSION,
        "source_workbook_sha256": workbook_hash,
        "accepted_record_count": len(records),
        "input_sha256": {
            "workbook": workbook_hash,
            "term_manifest": hashlib.sha256(files["term_manifest.csv"]).hexdigest(),
        },
        "output_sha256": {
            "canonical_records.csv": hashlib.sha256(
                files["canonical_records.csv"]
            ).hexdigest()
        },
        "skipped_row_count": len(skipped),
        "blocking_row_count": len(blocking),
    }
    files["source_manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    return OfferedCoursesBundle(files, workbook_hash, len(records), skipped, blocking)


def build_template() -> bytes:
    """The downloadable format: the source workbook's columns, one sheet per term."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "S1 AY2025-26"
    sheet.append(REQUIRED_COLUMNS)
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="DDE7F0")
    for cell in sheet[1]:
        cell.font = bold
        cell.fill = fill
    widths = (14, 14, 32, 14, 24, 10, 15, 15, 12, 28, 20, 18, 34, 30)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width
    sheet.freeze_panes = "A2"

    guide = workbook.create_sheet(INSTRUCTIONS_SHEET)
    guide.column_dimensions["A"].width = 110
    lines = [
        ("Offered Courses upload format", True),
        ("", False),
        ("One sheet per term, named like the registrar export:", True),
        ("  S1 AY2025-26   first semester of 2025-2026", False),
        ("  S2 AY2025-26   second semester of 2025-2026", False),
        ("  Summer 2026    summer term of 2025-2026", False),
        ("Each term must already exist on Term Setup and must not be the active term.", False),
        ("Sheets with other names, and this Instructions sheet, are ignored.", False),
        ("", False),
        ("Row 1 holds these columns, spelled exactly as in the template:", True),
        ("  " + ", ".join(REQUIRED_COLUMNS), False),
        ("", False),
        ("What each row needs:", True),
        ("  Group Number     the section number, e.g. 1", False),
        ("  Course Code      a code in the Subject catalog, e.g. CIS 1101", False),
        ("  Academic Period - Year   e.g. '1ST Semester - 2025', '2ND Semester - 2025', 'Summer - 2026'", False),
        ("  Course Status    DISSOLVED rows are skipped", False),
        ("  Teacher/s        SURNAME, GIVEN NAMES M. as on the Roster, e.g. DELA CRUZ, JUAN M.", False),
        ("  Schedule         <days> <start> - <end> <room>, e.g. MW 09:00 AM - 10:30 AM LB447TC", False),
        ("  The other columns may be left blank.", False),
        ("", False),
        ("An offering that meets more than once lists its first meeting on its own row", True),
        ("and each further meeting on the next row, with only the Schedule column filled.", True),
        ("", False),
        ("Skipped (listed, never block): dissolved courses, codes outside the Subject catalog,", False),
        ("rows with no teacher or no schedule, rows labelled with another term, duplicates.", False),
        ("Blocking (fix and upload again): a teacher with no Faculty record, an unreadable", False),
        ("schedule, or an invalid group number.", False),
    ]
    for text, strong in lines:
        guide.append([text])
        if strong:
            guide.cell(guide.max_row, 1).font = bold
    for row in guide.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=False)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
