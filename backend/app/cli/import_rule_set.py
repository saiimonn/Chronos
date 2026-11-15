"""Import a CHR-100 candidate package into local PostgreSQL as a draft RuleSet.

Run from ``backend/``:

    python -m app.cli.import_rule_set --artifact candidate_rule_set.json --dry-run
    python -m app.cli.import_rule_set --artifact candidate_rule_set.json

Re-publish the same package with per-teacher coverage under a display label
(the rules are identical; only the teacher coverage is added):

    python -m app.cli.import_rule_set --artifact candidate_rule_set.json \
        --faculty-metrics faculty_metrics.json --version-label 1.1 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.services.arm.faculty_metrics_package import (
    FacultyMetricsPackageError,
    attach_faculty_metrics,
    load_faculty_metrics_package,
)
from app.services.arm.importer import RulePackageImportError, import_rule_package
from app.services.arm.rule_package import load_rule_package


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--notes", help="optional review note stored on the draft")
    parser.add_argument(
        "--version-label",
        help='optional display name for the draft, e.g. "1.1"',
    )
    parser.add_argument(
        "--faculty-metrics",
        type=Path,
        help="faculty_metrics.json to attach in the same transaction",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the artifact and local references, then roll back",
    )
    args = parser.parse_args(argv)

    try:
        package = load_rule_package(args.artifact)
        metrics_package = (
            load_faculty_metrics_package(args.faculty_metrics)
            if args.faculty_metrics
            else None
        )
        artifact_hash = _sha256(args.artifact)
        notes = args.notes or (
            f"Offline import; artifact sha256={artifact_hash}"
        )
        metric_count = 0
        with SessionLocal() as session:
            summary = import_rule_package(
                session, package, notes=notes, version_label=args.version_label
            )
            if metrics_package is not None:
                metric_count = attach_faculty_metrics(
                    session, summary.rule_set_id, metrics_package
                )
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
    except (
        OSError,
        ValidationError,
        RulePackageImportError,
        FacultyMetricsPackageError,
        SQLAlchemyError,
    ) as error:
        print(f"candidate RuleSet import failed: {error}", file=sys.stderr)
        return 1

    action = "validated and rolled back" if args.dry_run else "imported"
    label = f" (label {summary.version_label})" if summary.version_label else ""
    print(
        f"{action} draft RuleSet v{summary.version}{label} "
        f"({summary.rule_set_id}): {summary.rule_count} rules, "
        f"{summary.training_term_count} training terms"
        + (f", {metric_count} faculty metric rows" if metrics_package else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
