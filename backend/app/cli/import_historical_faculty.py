"""Add every instructor named in the historical records to the faculty roster.

The roster held only the 28 instructors reviewed for research onboarding
(CHR-133). The past records name more people, many of whom no longer teach.
The project owner asked on 2026-11-09 for all of them to be on file. Each
missing instructor is added as **inactive**, so nobody is scheduled until an
administrator reviews the record and activates it on the Roster page.

What this script does and does not decide:

- Employment status (full- or part-time) is not in the historical records.
  New rows are stored as part-time because the column cannot be empty. That
  value is a placeholder: confirm it when activating the instructor.
- Availability starts unset and preferred days start empty, the same as a
  faculty member created through the API.
- Existing rows are never changed. An instructor already on the roster is
  matched first by the reviewed identity map (the CHR-133 onboarding sheet,
  which links a historical name to a faculty_id), then by display name.
- The reviewed identity map is required. Three reviewed instructors have a
  roster name that differs from their historical spelling, so importing
  without the map adds them a second time under a generated id. A missing,
  unreadable or empty map stops the command before any write.
- A new row's faculty_id is a UUIDv5 of the historical name, so re-running the
  script is a no-op and every installation gets the same ids.

Run it from `backend/` with the venv active, after `alembic upgrade head`::

    python -m app.cli.import_historical_faculty            # dry run, the default
    python -m app.cli.import_historical_faculty --apply

On a database that already holds the reviewed 28, the dry run must report 28
already on the roster and 81 to add. Anything else means the wrong database or
the wrong identity map; stop before `--apply`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Faculty
from app.services.ingestion.faculty_names import (  # noqa: F401 - re-exported
    HISTORICAL_FACULTY_NAMESPACE,
    _name_key,
    display_name,
    historical_faculty_id,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RECORDS = (
    REPOSITORY_ROOT / "dataset/preprocessed/2026-10-04-v1/canonical_records.csv"
)
DEFAULT_IDENTITY_MAP = (
    REPOSITORY_ROOT
    / "dataset/handoff/CHR-133-CHR-COLAB-007-2026-10-26-v2/faculty_onboarding.pending.csv"
)
class IdentityMapError(ValueError):
    """The reviewed identity map is required and could not be read."""


@dataclass(frozen=True)
class HistoricalInstructor:
    historical_name: str
    display_name: str
    record_count: int
    first_period: tuple[str, int]
    last_period: tuple[str, int]


@dataclass(frozen=True)
class ImportPlan:
    to_add: list[HistoricalInstructor]
    already_on_roster: list[tuple[HistoricalInstructor, str]]


def read_historical_instructors(rows: Iterable[dict[str, str]]) -> list[HistoricalInstructor]:
    seen: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        name = row["faculty_name"].strip()
        if not name:
            continue
        seen.setdefault(name, []).append((row["academic_year"], int(row["term_number"])))
    return [
        HistoricalInstructor(
            historical_name=name,
            display_name=display_name(name),
            record_count=len(periods),
            first_period=min(periods),
            last_period=max(periods),
        )
        for name, periods in sorted(seen.items())
    ]


def plan_import(
    instructors: Sequence[HistoricalInstructor],
    identity_map: dict[str, str],
    roster: dict[str, str],
) -> ImportPlan:
    """Split the instructors into those to add and those already on file.

    `identity_map` maps a historical name to a reviewed faculty_id.
    `roster` maps each existing faculty_id to its full name.
    """

    roster_by_name = {_name_key(name): fid for fid, name in roster.items()}
    to_add: list[HistoricalInstructor] = []
    existing: list[tuple[HistoricalInstructor, str]] = []
    # Two historical spellings that read as the same person ("DOE, JOHN .."
    # and "DOE, JOHN") must become one roster row, not two.
    queued_names: set[str] = set()
    for instructor in instructors:
        if _name_key(instructor.display_name) in queued_names:
            continue
        mapped = identity_map.get(instructor.historical_name)
        if mapped is not None and mapped in roster:
            existing.append((instructor, mapped))
            continue
        generated = str(historical_faculty_id(instructor.historical_name))
        if generated in roster:
            existing.append((instructor, generated))
            continue
        by_name = roster_by_name.get(_name_key(instructor.display_name))
        if by_name is not None:
            existing.append((instructor, by_name))
            continue
        to_add.append(instructor)
        queued_names.add(_name_key(instructor.display_name))
    return ImportPlan(to_add=to_add, already_on_roster=existing)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_identity_map(path: Path | None) -> dict[str, str]:
    if path is None:
        raise IdentityMapError("a reviewed faculty identity map is required")
    if not path.is_file():
        raise IdentityMapError(f"reviewed faculty identity map not found: {path}")
    try:
        rows = _read_csv(path)
    except (OSError, UnicodeError, csv.Error) as error:
        raise IdentityMapError(f"{path}: cannot read identity map ({error})") from error
    if rows and not {"historical_faculty_name", "faculty_id"} <= set(rows[0]):
        raise IdentityMapError(
            f"{path}: expected historical_faculty_name and faculty_id columns"
        )
    mapping = {
        row["historical_faculty_name"].strip(): row["faculty_id"].strip()
        for row in rows
        if row["historical_faculty_name"].strip() and row["faculty_id"].strip()
    }
    if not mapping:
        raise IdentityMapError(f"{path}: no reviewed faculty identities found")
    return mapping


def apply_plan(db: Session, plan: ImportPlan) -> None:
    for instructor in plan.to_add:
        db.add(
            Faculty(
                faculty_id=historical_faculty_id(instructor.historical_name),
                full_name=instructor.display_name,
                # Placeholder: the records do not say. Confirm on activation.
                is_full_time=False,
                is_active=False,
                availability_mask="",
                availability_reviewed_term_id=None,
            )
        )


def _period(period: tuple[str, int]) -> str:
    return f"AY {period[0]} T{period[1]}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument("--identity-map", type=Path, default=DEFAULT_IDENTITY_MAP)
    parser.add_argument(
        "--apply", action="store_true", help="write the new rows (default is a dry run)"
    )
    parser.add_argument(
        "--write-mapping",
        type=Path,
        help="once every instructor is on the roster, write the import_history "
        "faculty mapping (faculty_name,faculty_id) with this database's ids",
    )
    args = parser.parse_args(argv)

    instructors = read_historical_instructors(_read_csv(args.records))
    try:
        identity_map = read_identity_map(args.identity_map)
    except IdentityMapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"{len(identity_map)} reviewed faculty identities loaded from "
        f"{args.identity_map}."
    )

    with SessionLocal() as db:
        roster = {
            str(fid): name
            for fid, name in db.execute(select(Faculty.faculty_id, Faculty.full_name))
        }
        plan = plan_import(instructors, identity_map, roster)
        print(
            f"{len(instructors)} instructors in the historical records; "
            f"{len(plan.already_on_roster)} already on the roster; "
            f"{len(plan.to_add)} to add as inactive."
        )
        for instructor in plan.to_add:
            print(
                f"  + {instructor.display_name:<45} "
                f"({instructor.historical_name}; {instructor.record_count} records, "
                f"{_period(instructor.first_period)} – {_period(instructor.last_period)})"
            )
        if args.write_mapping is not None:
            # The ids differ per database, so the mapping must come from the
            # database being imported into, never from another machine.
            if plan.to_add:
                print(
                    "error: add the missing instructors (--apply) before "
                    "writing the mapping",
                    file=sys.stderr,
                )
                return 2
            with args.write_mapping.open("w", encoding="utf-8", newline="") as out:
                writer = csv.writer(out)
                writer.writerow(("faculty_name", "faculty_id"))
                for instructor, faculty_id in plan.already_on_roster:
                    writer.writerow((instructor.historical_name, faculty_id))
            print(
                f"Wrote {len(plan.already_on_roster)} mappings to "
                f"{args.write_mapping}."
            )
        if not args.apply:
            print("Dry run: nothing written. Re-run with --apply to add them.")
            return 0
        apply_plan(db, plan)
        db.commit()
        print(f"Added {len(plan.to_add)} inactive faculty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
