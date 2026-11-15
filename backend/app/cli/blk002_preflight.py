"""Report what would stop the CHR-BLK-002 cutover, and fix the one part it may.

Run this before `alembic upgrade head`:

    python -m app.cli.blk002_preflight              # report only
    python -m app.cli.blk002_preflight --apply      # create missing offerings
    python -m app.cli.blk002_preflight              # must exit 0

Reporting is read-only and the exit code is the answer: zero when the cutover
can proceed, one when anything remains. `--apply` writes exactly one kind of
row — a Subject Offering for a subject that has none in its term — through the
same service the API uses, so Group Nos. stay sequential and nothing forks the
numbering. Every other defect needs a person to decide, and the migration
re-checks all of them itself.

The queries read `course_section`, so this only runs before the migration; once
the cutover is applied the table is gone and the command has nothing to check.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain import SubjectType
from app.models import Subject, SubjectOffering
from app.services.catalog.offerings import add_next_group
from app.services.migration.block_preflight import (
    Defect,
    DefectKind,
    LegacySection,
    find_defects,
)

# Ordered so a rerun after `--apply` reads the same way as the first run.
_SECTION_QUERY = text(
    """
    SELECT section_id, term_id, subject_code, program_code, year_level,
           section_letter, expected_enrollment
    FROM course_section
    ORDER BY term_id, subject_code, program_code, year_level, section_letter
    """
)
_SCHEDULE_COUNT_QUERY = text(
    """
    SELECT section_id, count(*)
    FROM schedule_record
    WHERE section_id IS NOT NULL
    GROUP BY section_id
    """
)


def collect_defects(session: Session) -> list[Defect]:
    sections = [
        LegacySection(**row._mapping) for row in session.execute(_SECTION_QUERY)
    ]
    offering_counts = {
        (term_id, subject_code): count
        for term_id, subject_code, count in session.execute(
            select(
                SubjectOffering.term_id,
                SubjectOffering.subject_code,
                func.count(),
            ).group_by(SubjectOffering.term_id, SubjectOffering.subject_code)
        )
    }
    subject_types = {
        code: SubjectType(subject_type)
        for code, subject_type in session.execute(
            select(Subject.subject_code, Subject.subject_type)
        )
    }
    schedule_counts = dict(session.execute(_SCHEDULE_COUNT_QUERY).all())
    return find_defects(
        sections,
        offering_counts=offering_counts,
        subject_types=subject_types,
        schedule_counts=schedule_counts,
    )


def create_missing_offerings(session: Session, defects: Sequence[Defect]) -> int:
    """Give every subject that has no offering in its term a first group."""
    created = 0
    for defect in defects:
        if defect.kind is not DefectKind.MISSING_OFFERING:
            continue
        add_next_group(session, defect.term_id, defect.subject_code)
        created += 1
    session.commit()
    return created


def format_report(defects: Sequence[Defect]) -> str:
    if not defects:
        return "No defects found; the Block cutover can run."
    lines = [f"{len(defects)} defect(s) block the Block cutover:", ""]
    for defect in defects:
        subject = defect.subject_code or "-"
        cohort = (
            f"{defect.cohort[1]}-{defect.cohort[2]}{defect.cohort[3]}"
            if defect.cohort
            else "-"
        )
        lines.append(
            f"  {defect.kind}  term={defect.term_id}  subject={subject}  "
            f"block={cohort}"
        )
        lines.append(f"      {defect.detail}")
        lines.append(
            f"      {len(defect.section_ids)} section(s), "
            f"{defect.schedule_record_count} schedule record(s)"
            + ("  [--apply fixes this]" if defect.is_fixable else "")
        )
    return "\n".join(lines)


def as_json(defects: Sequence[Defect]) -> str:
    return json.dumps(
        [
            {
                "kind": str(defect.kind),
                "term_id": str(defect.term_id),
                "subject_code": defect.subject_code,
                "cohort": [str(part) for part in defect.cohort]
                if defect.cohort
                else None,
                "detail": defect.detail,
                "section_ids": [str(value) for value in defect.section_ids],
                "schedule_record_count": defect.schedule_record_count,
                "fixable": defect.is_fixable,
            }
            for defect in defects
        ],
        indent=2,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create the missing Subject Offerings, then report what remains",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the report as JSON, to keep as evidence",
    )
    arguments = parser.parse_args(argv)

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            defects = collect_defects(session)
            if arguments.apply:
                created = create_missing_offerings(session, defects)
                print(f"created {created} Subject Offering(s)")
                defects = collect_defects(session)
    finally:
        engine.dispose()

    print(format_report(defects))
    if arguments.json is not None:
        arguments.json.write_text(as_json(defects), encoding="utf-8")
        print(f"wrote {arguments.json}")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
