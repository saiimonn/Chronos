"""Load the reference catalogs a fresh installation needs before anyone can use it.

Subjects, course programs, and curricula are reference data: identical for
every installation, rarely changed, and knowable before deployment. A fresh
`supabase start` leaves their tables empty, and typing the whole DCISM catalog
into forms is not a reasonable first-run experience.

This is a script rather than a data migration on purpose. Migrations in this
repository are frozen once shipped, as `app/alembic_support/canonical_slots.py`
states, and reference data needs corrections — a mistyped code, wrong units, a
subject classified LEC that should be LAB. Each correction would otherwise cost
a migration. Here it costs a CSV edit and a re-run.

Every write is an upsert on the row's natural key, so running this twice is the
same as running it once, and re-running after an edit corrects that row alone.
Curricula and their requirements are reconciled rather than upserted: for
every program course_programs.csv names, curriculum.csv is the complete list of
its curriculum versions and their placements. Moving or removing a row takes
effect, and a version the file no longer names at all is deleted.

Run it from `backend/` with the venv active, after the schema is up to date::

    alembic upgrade head
    python -m app.cli.seed_catalog [--data-dir data] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain import academic_year_start
from app.models import (
    CourseProgram,
    Curriculum,
    CurriculumRequirement,
    Subject,
)
from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL
from app.models.curriculum import MAX_TERM_NUMBER, MIN_TERM_NUMBER

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

PROGRAMS_FILE = "course_programs.csv"
SUBJECTS_FILE = "subjects.csv"
CURRICULUM_FILE = "curriculum.csv"

SUBJECT_TYPES = ("LEC", "LAB")


class SeedDataError(Exception):
    """The CSVs do not describe a catalog that can be written.

    Carries every violation rather than the first, because someone fixing a
    hand-built CSV should see the whole list in one run instead of discovering
    the next fault after each correction.
    """

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"{len(violations)} problem(s) in the seed data")
        self.violations = violations


@dataclass
class SeedCounts:
    """What a run changed, counted separately so a no-op run is visible as one."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0

    def record(self, *, existed: bool, changed: bool) -> None:
        if not existed:
            self.created += 1
        elif changed:
            self.updated += 1
        else:
            self.unchanged += 1

    def __str__(self) -> str:
        text = (
            f"{self.created} created, {self.updated} updated, "
            f"{self.unchanged} unchanged"
        )
        if self.removed:
            text += f", {self.removed} removed"
        return text


@dataclass
class SeedReport:
    programs: SeedCounts = field(default_factory=SeedCounts)
    subjects: SeedCounts = field(default_factory=SeedCounts)
    curricula: SeedCounts = field(default_factory=SeedCounts)
    requirements: SeedCounts = field(default_factory=SeedCounts)

    def lines(self) -> list[str]:
        return [
            f"course programs:       {self.programs}",
            f"subjects:              {self.subjects}",
            f"curricula:             {self.curricula}",
            f"curriculum requirements: {self.requirements}",
        ]


@dataclass(frozen=True)
class Row:
    """One CSV record, keeping the file line number for error reporting."""

    line: int
    values: dict[str, str]

    def get(self, column: str) -> str:
        return (self.values.get(column) or "").strip()


def parse_rows(
    path: Path, expected_columns: tuple[str, ...], violations: list[str]
) -> list[Row]:
    """Every well-formed data row of a CSV, with `#` comments and blank lines dropped.

    Structural faults — a missing file, a wrong header, a row with the wrong
    number of columns — are appended to `violations` instead of raised, so one
    run reports every malformed row in every file. A row with the wrong column
    count is left out of the result; the rest of the file is still read.

    Comments are stripped before the reader sees them so the templates can
    carry their own documentation without a header-detection special case.
    """

    if not path.exists():
        violations.append(f"{path.name}: file not found at {path}")
        return []

    with path.open(newline="", encoding="utf-8") as handle:
        numbered = [
            (number, text)
            for number, text in enumerate(handle.read().splitlines(), start=1)
        ]

    meaningful = [
        (number, text)
        for number, text in numbered
        if text.strip() and not text.lstrip().startswith("#")
    ]
    if not meaningful:
        violations.append(f"{path.name}: no header row")
        return []

    header_line, header_text = meaningful[0]
    header = [column.strip() for column in next(csv.reader([header_text]))]
    if tuple(header) != expected_columns:
        # Without a trusted header no column can be named, so the data rows
        # of this file cannot be checked; the other files still are.
        violations.append(
            f"{path.name}:{header_line}: header is {header}, "
            f"expected {list(expected_columns)}"
        )
        return []

    rows: list[Row] = []
    for number, text in meaningful[1:]:
        fields = next(csv.reader([text]))
        if len(fields) != len(expected_columns):
            violations.append(
                f"{path.name}:{number}: has {len(fields)} column(s), "
                f"expected {len(expected_columns)}"
            )
            continue
        rows.append(Row(line=number, values=dict(zip(expected_columns, fields))))
    return rows


def read_rows(path: Path, expected_columns: tuple[str, ...]) -> list[Row]:
    """`parse_rows` for a single file, raising every structural fault it found."""

    violations: list[str] = []
    rows = parse_rows(path, expected_columns, violations)
    if violations:
        raise SeedDataError(violations)
    return rows


def _require_nonblank(
    row: Row, column: str, filename: str, violations: list[str]
) -> str:
    value = row.get(column)
    if not value:
        violations.append(f"{filename}:{row.line}: {column} is blank")
    return value


def _require_max_length(
    value: str, limit: int, row: Row, column: str, filename: str, violations: list[str]
) -> None:
    if len(value) > limit:
        violations.append(
            f"{filename}:{row.line}: {column} is {len(value)} characters, "
            f"maximum is {limit}"
        )


def _require_int_in_range(
    row: Row,
    column: str,
    low: int,
    high: int,
    filename: str,
    violations: list[str],
) -> int | None:
    raw = row.get(column)
    try:
        value = int(raw)
    except ValueError:
        violations.append(f"{filename}:{row.line}: {column} is not an integer: {raw!r}")
        return None
    if not low <= value <= high:
        violations.append(
            f"{filename}:{row.line}: {column} is {value}, must be {low} to {high}"
        )
        return None
    return value


def _reject_duplicate(
    seen: dict[tuple, int],
    key: tuple,
    row: Row,
    filename: str,
    description: str,
    violations: list[str],
) -> None:
    first = seen.get(key)
    if first is not None:
        violations.append(
            f"{filename}:{row.line}: duplicate {description}, "
            f"already given on line {first}"
        )
        return
    seen[key] = row.line


def validate(
    program_rows: list[Row], subject_rows: list[Row], curriculum_rows: list[Row]
) -> None:
    """Check every row before anything is written.

    Nothing reaches the database until this passes, so a bad CSV leaves the
    catalog exactly as it was rather than half-replaced.
    """

    violations: list[str] = []

    program_codes: dict[tuple, int] = {}
    for row in program_rows:
        code = _require_nonblank(row, "program_code", PROGRAMS_FILE, violations)
        name = _require_nonblank(row, "program_name", PROGRAMS_FILE, violations)
        _require_max_length(code, 20, row, "program_code", PROGRAMS_FILE, violations)
        _require_max_length(name, 150, row, "program_name", PROGRAMS_FILE, violations)
        if code:
            _reject_duplicate(
                program_codes, (code,), row, PROGRAMS_FILE, "program_code", violations
            )

    subject_codes: dict[tuple, int] = {}
    for row in subject_rows:
        code = _require_nonblank(row, "subject_code", SUBJECTS_FILE, violations)
        title = _require_nonblank(row, "subject_title", SUBJECTS_FILE, violations)
        _require_max_length(code, 20, row, "subject_code", SUBJECTS_FILE, violations)
        _require_max_length(title, 150, row, "subject_title", SUBJECTS_FILE, violations)

        units_raw = row.get("units")
        try:
            if int(units_raw) <= 0:
                violations.append(
                    f"{SUBJECTS_FILE}:{row.line}: units must be positive: {units_raw!r}"
                )
        except ValueError:
            violations.append(
                f"{SUBJECTS_FILE}:{row.line}: units is not an integer: {units_raw!r}"
            )

        subject_type = row.get("subject_type")
        if subject_type not in SUBJECT_TYPES:
            violations.append(
                f"{SUBJECTS_FILE}:{row.line}: subject_type is {subject_type!r}, "
                f"must be one of {list(SUBJECT_TYPES)}"
            )
        if code:
            _reject_duplicate(
                subject_codes, (code,), row, SUBJECTS_FILE, "subject_code", violations
            )

    placements: dict[tuple, int] = {}
    for row in curriculum_rows:
        program_code = _require_nonblank(
            row, "program_code", CURRICULUM_FILE, violations
        )
        subject_code = _require_nonblank(
            row, "subject_code", CURRICULUM_FILE, violations
        )
        effective_year = row.get("effective_year")

        try:
            academic_year_start(effective_year)
        except ValueError as error:
            violations.append(f"{CURRICULUM_FILE}:{row.line}: {error}")

        year_level = _require_int_in_range(
            row,
            "year_level",
            MIN_YEAR_LEVEL,
            MAX_YEAR_LEVEL,
            CURRICULUM_FILE,
            violations,
        )
        term_number = _require_int_in_range(
            row,
            "term_number",
            MIN_TERM_NUMBER,
            MAX_TERM_NUMBER,
            CURRICULUM_FILE,
            violations,
        )

        # Cross-file references are checked against the CSVs, not the database.
        # A curriculum row may only name a subject this same run also seeds, so
        # the three files stay consistent as one unit.
        if program_code and (program_code,) not in program_codes:
            violations.append(
                f"{CURRICULUM_FILE}:{row.line}: unknown program_code "
                f"{program_code!r}, not in {PROGRAMS_FILE}"
            )
        if subject_code and (subject_code,) not in subject_codes:
            violations.append(
                f"{CURRICULUM_FILE}:{row.line}: unknown subject_code "
                f"{subject_code!r}, not in {SUBJECTS_FILE}"
            )

        if program_code and subject_code and year_level and term_number:
            _reject_duplicate(
                placements,
                (program_code, effective_year, year_level, term_number, subject_code),
                row,
                CURRICULUM_FILE,
                "requirement",
                violations,
            )

    if violations:
        raise SeedDataError(violations)


def _apply(instance: object, values: dict[str, object]) -> bool:
    """Set only the fields that differ, reporting whether anything changed."""

    changed = False
    for field_name, value in values.items():
        if getattr(instance, field_name) != value:
            setattr(instance, field_name, value)
            changed = True
    return changed


def seed_programs(db: Session, rows: list[Row], counts: SeedCounts) -> None:
    for row in rows:
        code = row.get("program_code")
        program = db.get(CourseProgram, code)
        existed = program is not None
        if program is None:
            program = CourseProgram(program_code=code)
            db.add(program)
        changed = _apply(program, {"program_name": row.get("program_name")})
        counts.record(existed=existed, changed=changed)


def seed_subjects(db: Session, rows: list[Row], counts: SeedCounts) -> None:
    for row in rows:
        code = row.get("subject_code")
        subject = db.get(Subject, code)
        existed = subject is not None
        if subject is None:
            subject = Subject(subject_code=code)
            db.add(subject)
        changed = _apply(
            subject,
            {
                "subject_title": row.get("subject_title"),
                "units": int(row.get("units")),
                "subject_type": row.get("subject_type"),
            },
        )
        counts.record(existed=existed, changed=changed)


def seed_curriculum(
    db: Session, rows: list[Row], program_codes: set[str], report: SeedReport
) -> None:
    """Make the stored curricula of `program_codes` match the rows exactly.

    A curriculum is identified by (program_code, effective_year) and holds
    nothing else, so one the rows name is only ever created or left alone —
    there is no third column that could need updating. One the rows do not
    name is deleted with its requirements.

    For the programs the seed covers, the rows are the complete list of
    curriculum versions. Discovering versions from the rows alone would miss
    exactly the ones that need removing: a version whose last placement was
    deleted, or whose effective_year was corrected on every row. Left behind,
    such a version stays selectable by cohort resolution, which would keep
    choosing the obsolete prospectus. A version is retired by deleting its
    rows; there is no separate declaration to keep in step. Programs the seed
    does not name are not touched.
    """

    curricula: dict[tuple[str, str], Curriculum] = {}
    for row in rows:
        key = (row.get("program_code"), row.get("effective_year"))
        if key in curricula:
            continue
        curriculum = db.scalars(
            select(Curriculum).where(
                Curriculum.program_code == key[0],
                Curriculum.effective_year == key[1],
            )
        ).one_or_none()
        existed = curriculum is not None
        if curriculum is None:
            curriculum = Curriculum(program_code=key[0], effective_year=key[1])
            db.add(curriculum)
            db.flush()
        curricula[key] = curriculum
        report.curricula.record(existed=existed, changed=False)

    obsolete = [
        curriculum
        for curriculum in db.scalars(
            select(Curriculum).where(Curriculum.program_code.in_(program_codes))
        )
        if (curriculum.program_code, curriculum.effective_year) not in curricula
    ]
    for curriculum in obsolete:
        # Counted before the delete: the database cascade removes them, so the
        # session never loads them and cannot count them afterwards.
        report.requirements.removed += len(
            db.scalars(
                select(CurriculumRequirement.requirement_id).where(
                    CurriculumRequirement.curriculum_id == curriculum.curriculum_id
                )
            ).all()
        )
        db.delete(curriculum)
        report.curricula.removed += 1

    wanted: dict[tuple[str, str], set[tuple[int, int, str]]] = {
        key: set() for key in curricula
    }
    for row in rows:
        wanted[(row.get("program_code"), row.get("effective_year"))].add(
            (
                int(row.get("year_level")),
                int(row.get("term_number")),
                row.get("subject_code"),
            )
        )

    # The CSV is the whole truth for every curriculum it names. Upserting alone
    # would leave a moved or deleted row's old placement behind, and the
    # resolved curriculum would then require both the stale and the corrected
    # subject.
    for key, placements in wanted.items():
        curriculum = curricula[key]
        existing = {
            (
                requirement.year_level,
                requirement.term_number,
                requirement.subject_code,
            ): requirement
            for requirement in db.scalars(
                select(CurriculumRequirement).where(
                    CurriculumRequirement.curriculum_id == curriculum.curriculum_id
                )
            )
        }
        for placement in sorted(placements):
            year_level, term_number, subject_code = placement
            if placement not in existing:
                db.add(
                    CurriculumRequirement(
                        curriculum_id=curriculum.curriculum_id,
                        year_level=year_level,
                        term_number=term_number,
                        subject_code=subject_code,
                    )
                )
            # Every column of a requirement is part of its identity, so a row
            # that exists is already correct and nothing can be updated in place.
            report.requirements.record(existed=placement in existing, changed=False)
        for placement, requirement in existing.items():
            if placement not in placements:
                db.delete(requirement)
                report.requirements.removed += 1


def seed(db: Session, data_dir: Path) -> SeedReport:
    """Validate all three files, then write them in one transaction."""

    # All three files are parsed before any fault is raised, so structural
    # problems in several files surface together. Content checks wait until
    # parsing is clean: a file that failed to parse would otherwise report its
    # own codes as unknown in every file that references it.
    violations: list[str] = []
    program_rows = parse_rows(
        data_dir / PROGRAMS_FILE, ("program_code", "program_name"), violations
    )
    subject_rows = parse_rows(
        data_dir / SUBJECTS_FILE,
        ("subject_code", "subject_title", "units", "subject_type"),
        violations,
    )
    curriculum_rows = parse_rows(
        data_dir / CURRICULUM_FILE,
        (
            "program_code",
            "effective_year",
            "year_level",
            "term_number",
            "subject_code",
        ),
        violations,
    )
    if violations:
        raise SeedDataError(violations)

    validate(program_rows, subject_rows, curriculum_rows)

    report = SeedReport()
    seed_programs(db, program_rows, report.programs)
    seed_subjects(db, subject_rows, report.subjects)
    db.flush()
    seed_curriculum(
        db,
        curriculum_rows,
        {row.get("program_code") for row in program_rows},
        report,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"directory holding the three CSVs (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report what would change, then roll back",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with SessionLocal() as db:
        try:
            report = seed(db, args.data_dir)
        except SeedDataError as error:
            print(f"{error}:", file=sys.stderr)
            for violation in error.violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    for line in report.lines():
        print(line)
    if args.dry_run:
        print("dry run: rolled back, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
