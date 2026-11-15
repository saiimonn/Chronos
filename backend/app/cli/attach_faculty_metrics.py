"""Attach a faculty-metrics package to an existing RuleSet version.

Adds each teacher's best candidate pattern per stream (diagnostic rows for the
Rule Sets page). The version's rules, thresholds and terms are not changed.
Run from ``backend/``:

    python -m app.cli.attach_faculty_metrics --rule-set-id <uuid> \
        --artifact faculty_metrics.json --dry-run
    python -m app.cli.attach_faculty_metrics --rule-set-id <uuid> \
        --artifact faculty_metrics.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.services.arm.faculty_metrics_package import (
    FacultyMetricsPackageError,
    attach_faculty_metrics,
    load_faculty_metrics_package,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rule-set-id", type=UUID, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the package against the rule set, then roll back",
    )
    args = parser.parse_args(argv)

    try:
        package = load_faculty_metrics_package(args.artifact)
        with SessionLocal() as session:
            count = attach_faculty_metrics(session, args.rule_set_id, package)
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
    except (
        OSError,
        ValidationError,
        FacultyMetricsPackageError,
        SQLAlchemyError,
    ) as error:
        print(f"faculty metrics attach failed: {error}", file=sys.stderr)
        return 1

    action = "validated and rolled back" if args.dry_run else "attached"
    print(f"{action} {count} faculty metric rows to RuleSet {args.rule_set_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
