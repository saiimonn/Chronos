"""CHR-108 / CHR-ING-001 deterministic synthetic historical bootstrap files."""

import csv
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

FACULTY_ID = UUID("20000000-0000-0000-0000-000000000108")
FACULTY_NAME = "FACULTY A"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=tuple(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def refresh_manifest(dataset_dir: Path) -> None:
    """Rehash edited CSV bytes without changing provenance or the accepted count."""
    path = dataset_dir / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["output_sha256"]["canonical_records.csv"] = hashlib.sha256(
        (dataset_dir / "canonical_records.csv").read_bytes()
    ).hexdigest()
    manifest["input_sha256"]["term_manifest"] = hashlib.sha256(
        (dataset_dir / "term_manifest.csv").read_bytes()
    ).hexdigest()
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_bootstrap_dataset(
    path: Path, faculty_id: UUID = FACULTY_ID, *, continuation: bool = False,
) -> tuple[Path, Path]:
    """Write only synthetic inputs; callers supply any catalog ID needed by their test."""
    dataset_dir = path / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    workbook_hash = hashlib.sha256(
        b"CHR-108 / CHR-ING-001 synthetic workbook"
    ).hexdigest()
    records = []
    for term, days, start, end in (
        (1, "MWF", "08:00", "09:30"),
        (2, "TTh", "13:00", "14:30"),
    ):
        sheet = f"SYNTHETIC TERM {term}"
        offering_row, source_row = term * 10, term * 10 + 1
        records.append(
            {
                "historical_record_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"chronos:{workbook_hash}:{sheet}:{offering_row}:{source_row}",
                    )
                ),
                "source_workbook_sha256": workbook_hash,
                "source_sheet": sheet,
                "source_row": str(source_row),
                "offering_source_row": str(offering_row),
                "academic_year": "2023-2024",
                "term_number": str(term),
                "group_number": "1",
                "canonical_course_code": "SUBJ-1",
                "course_title": "Synthetic Subject A",
                "faculty_name": FACULTY_NAME,
                "meeting_days": days,
                "start_time": start,
                "end_time": end,
                "room_code": "ROOM-1",
                "teacher_subject_eligible": "1",
                "full_stream_eligible": "1",
            }
        )
    if continuation:
        record = records[0] | {
            "source_row": "12", "meeting_days": "Sa", "room_code": "ROOM-2",
        }
        record["historical_record_id"] = str(uuid5(
            NAMESPACE_URL,
            f"chronos:{workbook_hash}:{record['source_sheet']}:10:12",
        ))
        records.append(record)
    terms = [
        {
            "source_sheet": f"SYNTHETIC TERM {term}",
            "academic_year": "2023-2024",
            "term_number": str(term),
            "complete": "true",
            "include_in_research": "true",
            "exclusion_reason": "",
        }
        for term in (1, 2, 3)
    ]
    _write_csv(dataset_dir / "canonical_records.csv", records)
    _write_csv(dataset_dir / "term_manifest.csv", terms)
    crosswalk_path = path / "faculty_crosswalk.csv"
    _write_csv(
        crosswalk_path, [{"faculty_name": FACULTY_NAME, "faculty_id": str(faculty_id)}]
    )
    (dataset_dir / "source_manifest.json").write_text(
        json.dumps(
            {
                "accepted_record_count": len(records),
                "source_workbook_sha256": workbook_hash,
                "output_sha256": {},
                "input_sha256": {},
            }
        ),
        encoding="utf-8",
    )
    refresh_manifest(dataset_dir)
    return dataset_dir, crosswalk_path


def rewrite_as_teacher_subject_tier(dataset_dir: Path, *, keep_placement: bool) -> None:
    """Move the bundle to 2019-2020, inside the teacher-subject-only tier
    (FR-ING-002), optionally dropping the placement columns altogether."""
    for filename in ("canonical_records.csv", "term_manifest.csv"):
        path = dataset_dir / filename
        with path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields, rows = list(reader.fieldnames), list(reader)
        for row in rows:
            row["academic_year"] = "2019-2020"
            if "full_stream_eligible" in row:
                row["full_stream_eligible"] = "0"
        if filename == "canonical_records.csv" and not keep_placement:
            placement = ("meeting_days", "start_time", "end_time", "room_code")
            fields = [field for field in fields if field not in placement]
            rows = [{field: row[field] for field in fields} for row in rows]
        with path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    refresh_manifest(dataset_dir)
