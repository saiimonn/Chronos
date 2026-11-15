"""Load one synthetic, DCISM-sized term that is ready to generate.

`seed_users` and `seed_catalog` stop at accounts and reference catalogs, which
leaves a tester typing faculty, buildings, rooms, a term, blocks, offerings and
availability by hand before the GA has anything to schedule. This fills that
gap from the CSVs in `backend/data/scenario/`.

Every write goes through the same catalog services the API uses, so the term
gets its canonical slot grid, cohorts get their Blocks and offerings from the
curriculum, and each mask is saved and stamped as reviewed for the term exactly
as the availability dialog does it.

The data is synthetic: placeholder instructor names and invented buildings.

Idempotent. A record that already exists, matched on its natural key, is left
as it is, so a tester's edits to the seeded term survive a re-run. The command
refuses to run while a different term is active, because it would take over
term selection and re-stamp the seeded faculty's review markers; `--force`
allows that. Faculty that are not in the CSV are left alone, so on a database
that already has other faculty the term reports them as unreviewed until
someone reviews them.

Run it from `backend/` after `seed_catalog`::

    python -m app.cli.seed_catalog
    python -m app.cli.seed_scenario [--data-dir data/scenario] [--force]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.cli.seed_catalog import Row, SeedCounts, SeedDataError, parse_rows
from app.core.errors import APIError
from app.db.session import SessionLocal
from app.domain import DCISM_CALENDAR, canonical_room_code, generate_windows
from app.models import (
    Building,
    CourseProgram,
    Faculty,
    HistoricalSnapshot,
    Room,
    Term,
)
from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.schemas.catalog import BuildingCreate, RoomCreate
from app.schemas.faculty import FacultyCreate
from app.schemas.term import TermCreate
from app.services.catalog.availability import set_availability
from app.services.catalog.buildings import create_building
from app.services.catalog.cohort_generation import generate_cohort_classes
from app.services.catalog.curriculum import (
    NoApplicableCurriculumError,
    load_cohort_coverage,
    resolve_for_cohort,
)
from app.services.catalog.faculty import create_faculty
from app.services.catalog.rooms import create_room
from app.services.catalog.terms import create_term, select_term
from app.services.catalog.time_slots import list_time_slots
from app.services.curriculum_readiness import analyze_curriculum_coverage
from app.services.readiness import ReadinessBlocker, ReadinessReport, analyze_readiness

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "scenario"

#: The one term the scenario loads. Year levels 1-3 in 2026-2027 entered in
#: 2024-2026, which all resolve to the committed 2023-2024 curricula.
SCENARIO_ACADEMIC_YEAR = "2026-2027"
SCENARIO_TERM_NUMBER = 1

BUILDINGS_FILE = "buildings.csv"
ROOMS_FILE = "rooms.csv"
FACULTY_FILE = "faculty.csv"
COHORTS_FILE = "cohorts.csv"

BUILDING_COLUMNS = ("building_code", "building_name")
ROOM_COLUMNS = ("room_code", "building_code", "room_type", "expanded")
FACULTY_COLUMNS = ("full_name", "is_full_time", "available_from", "available_until")
COHORT_COLUMNS = ("program_code", "year_level", "section_letters")

_BOOLEANS = {"true": True, "false": False}


class ScenarioConflictError(Exception):
    """The database already schedules a different term."""


@dataclass
class ScenarioReport:
    term_id: UUID | None = None
    term_created: bool = False
    buildings: SeedCounts = field(default_factory=SeedCounts)
    rooms: SeedCounts = field(default_factory=SeedCounts)
    faculty: SeedCounts = field(default_factory=SeedCounts)
    availability: SeedCounts = field(default_factory=SeedCounts)
    blocks: SeedCounts = field(default_factory=SeedCounts)
    classes: SeedCounts = field(default_factory=SeedCounts)

    def lines(self) -> list[str]:
        state = "created" if self.term_created else "already present"
        return [
            f"term {SCENARIO_ACADEMIC_YEAR} T{SCENARIO_TERM_NUMBER}: {state} ({self.term_id})",
            f"buildings:    {self.buildings}",
            f"rooms:        {self.rooms}",
            f"faculty:      {self.faculty}",
            f"availability: {self.availability}",
            f"blocks:       {self.blocks}",
            f"classes:      {self.classes}",
        ]


@dataclass(frozen=True)
class ScenarioData:
    buildings: list[Row]
    rooms: list[Row]
    faculty: list[Row]
    cohorts: list[Row]


def _parse_time(raw: str) -> time | None:
    # HH:MM only: a UTC offset would make the slot comparison raise TypeError.
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return None


def _schema_violations(where: str, schema: type[BaseModel], **values: object) -> list[str]:
    """Run the API's own schema so a value it would refuse fails before any write."""
    try:
        schema(**values)
    except ValidationError as error:
        return [
            f"{where}: {'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors()
        ]
    return []


def load_data(data_dir: Path) -> ScenarioData:
    """Read and check every file before anything is written."""

    violations: list[str] = []
    data = ScenarioData(
        buildings=parse_rows(data_dir / BUILDINGS_FILE, BUILDING_COLUMNS, violations),
        rooms=parse_rows(data_dir / ROOMS_FILE, ROOM_COLUMNS, violations),
        faculty=parse_rows(data_dir / FACULTY_FILE, FACULTY_COLUMNS, violations),
        cohorts=parse_rows(data_dir / COHORTS_FILE, COHORT_COLUMNS, violations),
    )
    if violations:
        raise SeedDataError(violations)

    codes: set[str] = set()
    building_names: set[str] = set()
    for row in data.buildings:
        where = f"{BUILDINGS_FILE}:{row.line}"
        code = row.get("building_code")
        name = row.get("building_name")
        violations += _schema_violations(
            where, BuildingCreate, building_code=code, building_name=name
        )
        if code != code.upper():
            violations.append(f"{where}: building_code must be uppercase")
        if code in codes:
            violations.append(f"{where}: duplicate building_code")
        # The catalog folds case on names, so "Hall" and "HALL" collide.
        if name.lower() in building_names:
            violations.append(f"{where}: duplicate building_name")
        codes.add(code)
        building_names.add(name.lower())

    room_codes: set[str] = set()
    for row in data.rooms:
        where = f"{ROOMS_FILE}:{row.line}"
        if row.get("room_code") in room_codes:
            violations.append(f"{where}: duplicate room_code")
        room_codes.add(row.get("room_code"))
        if row.get("building_code") not in codes:
            violations.append(f"{where}: unknown building_code {row.get('building_code')!r}")
        if row.get("expanded") not in _BOOLEANS:
            violations.append(f"{where}: expanded must be true or false")
        violations += _schema_violations(
            where,
            RoomCreate,
            room_code=row.get("room_code"),
            building_name="-",
            room_type=row.get("room_type"),
        )

    windows = generate_windows(DCISM_CALENDAR)
    names: set[str] = set()
    for row in data.faculty:
        where = f"{FACULTY_FILE}:{row.line}"
        name = row.get("full_name")
        if name in names:
            violations.append(f"{where}: duplicate full_name")
        names.add(name)
        if row.get("is_full_time") not in _BOOLEANS:
            violations.append(f"{where}: is_full_time must be true or false")
        violations += _schema_violations(
            where, FacultyCreate, full_name=name, is_full_time=True
        )
        start = _parse_time(row.get("available_from"))
        end = _parse_time(row.get("available_until"))
        if start is None or end is None or start >= end:
            violations.append(
                f"{where}: available_from and available_until must be HH:MM, "
                "from before until"
            )
        elif not any(w.start_time >= start and w.end_time <= end for w in windows):
            violations.append(f"{where}: the availability window holds no time slot")

    for row in data.cohorts:
        where = f"{COHORTS_FILE}:{row.line}"
        try:
            year_level = int(row.get("year_level"))
        except ValueError:
            year_level = 0
        if not MIN_YEAR_LEVEL <= year_level <= MAX_YEAR_LEVEL:
            violations.append(
                f"{where}: year_level must be {MIN_YEAR_LEVEL} to {MAX_YEAR_LEVEL}"
            )
        if not row.get("program_code"):
            violations.append(f"{where}: program_code is blank")
        letters = row.get("section_letters").split()
        if not letters or not all(
            re.fullmatch(r"[A-Za-z0-9]{1,5}", letter) for letter in letters
        ):
            violations.append(
                f"{where}: section_letters must be space-separated, "
                "1 to 5 letters or digits each"
            )

    if violations:
        raise SeedDataError(violations)
    return data


def _check_against_database(db: Session, data: ScenarioData) -> None:
    """Refuse what the services would refuse mid-run, before the term is touched.

    The services commit as they go, so a fault found halfway through would
    leave the term already selected and the earlier records written.
    """

    if not db.scalar(select(func.count()).select_from(CourseProgram)):
        raise SeedDataError(
            ["no course programs found; run `python -m app.cli.seed_catalog` first"]
        )
    violations: list[str] = []
    # select_term refuses a captured term, and create_term would already have
    # committed it by then.
    archived = db.scalar(
        select(HistoricalSnapshot.snapshot_id)
        .outerjoin(Term, Term.term_id == HistoricalSnapshot.source_term_id)
        .where(
            or_(
                (HistoricalSnapshot.academic_year == SCENARIO_ACADEMIC_YEAR)
                & (HistoricalSnapshot.term_number == SCENARIO_TERM_NUMBER),
                (Term.academic_year == SCENARIO_ACADEMIC_YEAR)
                & (Term.term_number == SCENARIO_TERM_NUMBER),
            )
        )
        .limit(1)
    )
    if archived is not None:
        violations.append(
            f"term {SCENARIO_ACADEMIC_YEAR} T{SCENARIO_TERM_NUMBER} is a captured "
            "historical term and cannot be selected for scheduling"
        )
    for row in data.buildings:
        if db.scalar(
            select(Building.building_id).where(
                Building.building_code == row.get("building_code")
            )
        ):
            continue  # reused by code, never created, so its name cannot clash
        holder = db.scalar(
            select(Building.building_code).where(
                func.lower(Building.building_name) == row.get("building_name").lower()
            )
        )
        if holder is not None and holder != row.get("building_code"):
            violations.append(
                f"{BUILDINGS_FILE}:{row.line}: building_name is already used by "
                f"building {holder!r}"
            )
    for row in data.cohorts:
        program_code = row.get("program_code")
        if db.get(CourseProgram, program_code) is None:
            violations.append(
                f"{COHORTS_FILE}:{row.line}: unknown program_code {program_code!r}"
            )
            continue
        try:
            resolve_for_cohort(
                db, program_code, int(row.get("year_level")), SCENARIO_ACADEMIC_YEAR
            )
        except NoApplicableCurriculumError as error:
            violations.append(f"{COHORTS_FILE}:{row.line}: {error}")
    if violations:
        raise SeedDataError(violations)


def _ensure_term(db: Session, force: bool, report: ScenarioReport) -> Term:
    term = db.scalar(
        select(Term).where(
            Term.academic_year == SCENARIO_ACADEMIC_YEAR,
            Term.term_number == SCENARIO_TERM_NUMBER,
        )
    )
    active = db.scalar(select(Term).where(Term.is_active))
    if active is not None and (term is None or active.term_id != term.term_id) and not force:
        raise ScenarioConflictError(
            f"term {active.academic_year} T{active.term_number} is the current term; "
            f"seeding would make {SCENARIO_ACADEMIC_YEAR} T{SCENARIO_TERM_NUMBER} "
            "current instead and re-stamp the seeded faculty's reviews. "
            "Re-run with --force to do that anyway."
        )
    if term is None:
        term = create_term(
            db,
            TermCreate(
                academic_year=SCENARIO_ACADEMIC_YEAR,
                term_number=SCENARIO_TERM_NUMBER,
            ),
        )
        report.term_created = True
    if not term.is_active:
        term = select_term(db, term.term_id)
    report.term_id = term.term_id
    return term


def _seed_rooms(db: Session, data: ScenarioData, report: ScenarioReport) -> None:
    names: dict[str, str] = {}
    for row in data.buildings:
        code = row.get("building_code")
        building = db.scalar(select(Building).where(Building.building_code == code))
        report.buildings.record(existed=building is not None, changed=False)
        if building is None:
            building = create_building(
                db,
                BuildingCreate(building_code=code, building_name=row.get("building_name")),
            )
        names[code] = building.building_name

    for row in data.rooms:
        # Look the room up under the spelling create_room stores ("SAB201"),
        # or a rerun would miss it and try to create it again.
        code = canonical_room_code(row.get("building_code"), row.get("room_code"))
        existed = db.get(Room, code) is not None
        if not existed:
            create_room(
                db,
                RoomCreate(
                    room_code=code,
                    building_name=names[row.get("building_code")],
                    room_type=row.get("room_type"),
                    expanded=_BOOLEANS[row.get("expanded")],
                ),
            )
        report.rooms.record(existed=existed, changed=False)


def _seed_faculty(
    db: Session, term: Term, data: ScenarioData, report: ScenarioReport
) -> None:
    slots = list_time_slots(db, term.term_id)
    for row in data.faculty:
        name = row.get("full_name")
        faculty = db.scalar(
            select(Faculty).where(Faculty.full_name == name).order_by(Faculty.faculty_id)
        )
        existed = faculty is not None
        if faculty is None:
            faculty = create_faculty(
                db,
                FacultyCreate(
                    full_name=name, is_full_time=_BOOLEANS[row.get("is_full_time")]
                ),
            )
        report.faculty.record(existed=existed, changed=False)

        # A mask already reviewed for this term is someone's decision; keep it.
        reviewed = faculty.availability_reviewed_term_id == term.term_id
        if not reviewed:
            start = time.fromisoformat(row.get("available_from"))
            end = time.fromisoformat(row.get("available_until"))
            set_availability(
                db,
                faculty.faculty_id,
                term.term_id,
                [
                    slot.time_slot_id
                    for slot in slots
                    if slot.start_time >= start and slot.end_time <= end
                ],
            )
        report.availability.record(existed=reviewed, changed=False)


def _seed_cohorts(
    db: Session, term: Term, data: ScenarioData, report: ScenarioReport
) -> None:
    for row in data.cohorts:
        generated = generate_cohort_classes(
            db,
            term.term_id,
            row.get("program_code"),
            int(row.get("year_level")),
            row.get("section_letters").split(),
        )
        for block in generated.blocks:
            report.blocks.record(existed=not block.created, changed=False)
            for generated_class in block.classes:
                report.classes.record(existed=not generated_class.created, changed=False)


def seed_scenario(
    db: Session, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False
) -> ScenarioReport:
    """Load the scenario term. Services commit as they go, like the API."""

    data = load_data(data_dir)
    _check_against_database(db, data)
    report = ScenarioReport()
    term = _ensure_term(db, force, report)
    _seed_rooms(db, data, report)
    _seed_faculty(db, term, data, report)
    _seed_cohorts(db, term, data, report)
    return report


def scenario_readiness(
    db: Session, term_id: UUID
) -> tuple[ReadinessReport, tuple[ReadinessBlocker, ...]]:
    """Blockers and curriculum warnings, assembled as GET /readiness does."""

    problem = SQLAlchemySchedulingProblemProvider(db).get_problem(term_id)
    report = analyze_readiness(problem)
    warnings = analyze_curriculum_coverage(load_cohort_coverage(db, term_id))
    return report, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"directory holding the scenario CSVs (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="make the scenario term current even if another term is",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with SessionLocal() as db:
        try:
            report = seed_scenario(db, args.data_dir, force=args.force)
            readiness, warnings = scenario_readiness(db, report.term_id)
        except SeedDataError as error:
            print(f"{error}:", file=sys.stderr)
            for violation in error.violations:
                print(f"  {violation}", file=sys.stderr)
            return 1
        except ScenarioConflictError as error:
            print(f"refusing to seed: {error}", file=sys.stderr)
            return 2
        except APIError as error:
            db.rollback()
            print(f"{error.code}: {error.message} {error.details}", file=sys.stderr)
            return 1

    for line in report.lines():
        print(line)
    print(f"readiness: {'ready' if readiness.ready else 'NOT ready'}")
    for finding in (*readiness.blockers, *warnings):
        print(f"  {finding.code}: {finding.message}")
    return 0 if readiness.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
