"""CHR-108 / CHR-ING-001 privileged local historical bootstrap CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import engine
from app.services.ingestion.historical_file import (
    HistoricalImportError,
    load_historical_import,
)
from app.services.ingestion.historical_import import (
    import_historical_data,
    validate_historical_import,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--faculty-crosswalk", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="confirm administrator review before committing",
    )
    parser.add_argument(
        "--review-manifest",
        type=Path,
        help="reviewed JSON receipt from --validate-only",
    )
    args = parser.parse_args(argv)
    if args.commit and not args.confirm_reviewed:
        parser.error("--commit requires --confirm-reviewed")
    if args.commit and args.review_manifest is None:
        parser.error("--commit requires --review-manifest")
    try:
        data = load_historical_import(args.dataset_dir, args.faculty_crosswalk)
        if args.review_manifest is not None:
            try:
                receipt = json.loads(args.review_manifest.read_bytes())
                if receipt["action"] != "validated_read_only" or receipt[
                    "input_sha256"
                ] != dict(data.input_sha256):
                    raise ValueError("reviewed inputs differ")
            except (KeyError, TypeError, ValueError) as error:
                raise HistoricalImportError(
                    "review receipt is invalid or input hashes changed since validation"
                ) from error
        if engine.dialect.name != "postgresql":
            raise HistoricalImportError(
                "historical import requires local PostgreSQL 17"
            )
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=args.validate_only
        ) as connection, Session(bind=connection, autoflush=False) as session:
            version = session.scalar(text("SHOW server_version_num"))
            if int(version) // 10000 != 17:
                raise HistoricalImportError(
                    "historical import requires PostgreSQL 17"
                )
            if (
                args.validate_only
                and session.scalar(text("SHOW transaction_read_only")) != "on"
            ):
                raise HistoricalImportError("read-only validation was not enforced")
            validation = validate_historical_import(session, data)
            if args.commit:
                snapshot_ids = import_historical_data(session, data)
                session.commit()
            else:
                snapshot_ids = ()
                session.rollback()
    except HistoricalImportError as error:
        print(f"historical import failed: {error}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError) as error:
        print(
            f"historical import failed ({type(error).__name__}); details omitted",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "traceability": "CHR-108 / CHR-ING-001",
                "action": "imported" if args.commit else "validated_read_only",
                "record_count": len(data.records),
                "populated_period_count": len(data.periods),
                "empty_period_count": len(data.empty_periods),
                "unknown_historical_subject_count": validation.unknown_subject_count,
                "unknown_historical_room_count": validation.unknown_room_count,
                "snapshot_ids": [str(value) for value in snapshot_ids],
                "input_sha256": dict(data.input_sha256),
                "mining_or_activation_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
