"""Build a faculty-metrics package from a frozen CHR-COLAB-003 run.

For every teacher in each stream's training transactions, records the
candidate pattern that came closest to being retained and which thresholds it
met, using the same `best_faculty_patterns` the in-app miner uses. The output
is attached to the imported RuleSet with `app.cli.attach_faculty_metrics`.

Run from ``backend/``:

    .venv/bin/python -m scripts.colab_prototype.build_faculty_metrics \
        --evaluation-dir ../experiments/CHR-99-CHR-COLAB-003/<run> \
        --faculty-crosswalk ../experiments/CHR-100-CHR-COLAB-004/<run>/faculty_crosswalk.csv \
        --reference-aliases ../dataset/handoff/<dir>/reference_aliases.csv \
        --output ../experiments/CHR-100-CHR-COLAB-004/<run>/faculty_metrics.json

Faculty without a reviewed crosswalk entry keep their research key and get no
local faculty ID; no identity is guessed. Subject and room codes are mapped
through the reviewed aliases so they read like the imported rules.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.services.arm.faculty_metrics import best_faculty_patterns
from app.services.arm.mining import MiningThresholds
from app.services.arm.preprocessing import ARMTransaction, PatternStream


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_transactions(path: Path) -> tuple[ARMTransaction, ...]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        header = next(reader)
        item_columns = [
            (index, name) for index, name in enumerate(header) if "=" in name
        ]
        snapshot = header.index("snapshot_id")
        record = header.index("historical_record_id")
        return tuple(
            ARMTransaction(
                snapshot_id=UUID(row[snapshot]),
                historical_record_id=UUID(row[record]),
                items=tuple(name for index, name in item_columns if row[index] == "1"),
            )
            for row in reader
        )


def _read_pairs(path: Path, key: str, value: str) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as source:
        return {row[key]: row[value] for row in csv.DictReader(source)}


def build(evaluation_dir: Path, crosswalk: Path, aliases: Path | None) -> dict:
    thresholds_path = evaluation_dir / "selected_thresholds.json"
    identity_path = evaluation_dir / "faculty_identity_map.csv"
    inputs = [thresholds_path, identity_path, crosswalk]
    thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
    names = _read_pairs(identity_path, "research_faculty_key", "faculty_name")
    local_ids = _read_pairs(crosswalk, "research_faculty_key", "faculty_id")
    subject_alias: dict[str, str] = {}
    room_alias: dict[str, str] = {}
    if aliases is not None:
        inputs.append(aliases)
        with aliases.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if row["decision"] != "alias":
                    continue
                target = subject_alias if row["kind"] == "subject_code" else room_alias
                target[row["historical_code"]] = row["canonical_code"]

    streams: dict[str, dict] = {}
    metrics: list[dict] = []
    for stream in PatternStream:
        path = evaluation_dir / f"transactions_{stream.value}.csv"
        inputs.append(path)
        transactions = _read_transactions(path)
        selected = thresholds[stream.value]
        mining_thresholds = MiningThresholds(
            min_support=Decimal(selected["min_support"]),
            min_confidence=Decimal(selected["min_confidence"]),
            min_lift=Decimal(selected["min_lift"]),
            min_term_recurrence=int(selected["min_term_recurrence"]),
        )
        term_count = len({transaction.snapshot_id for transaction in transactions})
        streams[stream.value] = {
            "training_term_count": term_count,
            "transaction_count": len(transactions),
        }
        for metric in best_faculty_patterns(
            stream, transactions, mining_thresholds, term_count
        ):
            key = metric.faculty_key
            metrics.append(
                {
                    "stream": stream.value,
                    "source_faculty_key": key,
                    "faculty_name": names[key],
                    "faculty_id": local_ids.get(key),
                    "subject_code": subject_alias.get(
                        metric.subject_code, metric.subject_code
                    ),
                    "room_code": room_alias.get(metric.room_code, metric.room_code),
                    "meeting_days": metric.meeting_days,
                    "time_slot": metric.time_slot,
                    "offering_count": metric.offering_count,
                    "support": str(metric.support),
                    "confidence": str(metric.confidence),
                    "lift": str(metric.lift),
                    "term_recurrence": metric.term_recurrence,
                    "meets_support": metric.meets_support,
                    "meets_confidence": metric.meets_confidence,
                    "meets_lift": metric.meets_lift,
                    "meets_term_recurrence": metric.meets_term_recurrence,
                }
            )

    return {
        "schema_version": 1,
        "source_run": evaluation_dir.name,
        "input_sha256": {path.name: _sha256(path) for path in inputs},
        "thresholds": thresholds,
        "streams": streams,
        "metrics": metrics,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--faculty-crosswalk", type=Path, required=True)
    parser.add_argument("--reference-aliases", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    package = build(args.evaluation_dir, args.faculty_crosswalk, args.reference_aliases)
    args.output.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(package['metrics'])} faculty metrics to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
