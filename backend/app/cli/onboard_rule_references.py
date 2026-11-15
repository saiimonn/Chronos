"""Validate or atomically onboard CHR-133 reviewed local references."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import engine
from app.services.catalog.reference_onboarding import (
    ReferenceOnboardingError,
    build_validation_receipt,
    load_database_state,
    load_reviewed_reference_data,
    onboard_reviewed_references,
    validate_reviewed_references,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _repository_head() -> str:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise ReferenceOnboardingError(
            "repository must have exactly one Alembic head"
        )
    return heads[0]


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReferenceOnboardingError(
            "review receipt is not readable JSON"
        ) from error
    if not isinstance(payload, dict):
        raise ReferenceOnboardingError("review receipt must contain a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--reviewed-dir", type=Path, required=True)
    parser.add_argument("--approval-manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="confirm Department Administrator review before committing",
    )
    parser.add_argument(
        "--review-manifest",
        type=Path,
        help="JSON receipt emitted by --validate-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.commit and not args.confirm_reviewed:
        parser.error("--commit requires --confirm-reviewed")
    if args.commit and args.review_manifest is None:
        parser.error("--commit requires --review-manifest")

    try:
        data = load_reviewed_reference_data(
            args.source_dir, args.reviewed_dir, args.approval_manifest
        )
        receipt = _load_receipt(args.review_manifest) if args.commit else None
        options: dict[str, object] = {"isolation_level": "REPEATABLE READ"}
        if args.validate_only:
            options["postgresql_readonly"] = True
        with engine.connect().execution_options(**options) as connection, Session(
            bind=connection, autoflush=False
        ) as session:
            head = _repository_head()
            if args.validate_only:
                state = load_database_state(session)
                if state.alembic_revision != head:
                    raise ReferenceOnboardingError(
                        "database Alembic revision is not the repository head"
                    )
                if session.scalar(text("SHOW transaction_read_only")) != "on":
                    raise ReferenceOnboardingError(
                        "read-only validation was not enforced"
                    )
                if session.scalar(text("SHOW transaction_isolation")) != "repeatable read":
                    raise ReferenceOnboardingError(
                        "repeatable-read validation was not enforced"
                    )
                report = validate_reviewed_references(session, data)
                output = build_validation_receipt(data, state, report)
                session.rollback()
            else:
                assert receipt is not None
                receipt_revision = receipt.get("alembic_revision")
                if receipt_revision != head:
                    raise ReferenceOnboardingError(
                        "validated database Alembic revision is not the repository head"
                    )
                report = onboard_reviewed_references(session, data, receipt=receipt)
                session.commit()
                output = {
                    "schema_version": 1,
                    "traceability": "CHR-133 / CHR-COLAB-007",
                    "action": "onboarded",
                    "input_sha256": dict(data.input_sha256),
                    "created": dict(report.created),
                    "reused": dict(report.reused),
                    "rules_packaged_imported_or_activated": False,
                }
    except ReferenceOnboardingError as error:
        print(f"reference onboarding failed: {error}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError) as error:
        print(
            f"reference onboarding failed ({type(error).__name__}); details omitted",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
