"""CHR-98 / CHR-COLAB-002 build the explicit historical scope mapping."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd


PROGRAM_CODES = frozenset({"BSCS", "BSIS", "BSIT"})


def normalized(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).replace("\u00a0", " ").split()).strip()


def read_course_codes(workbook: Path) -> set[str]:
    sheets = pd.read_excel(workbook, sheet_name=None, dtype=str, engine="openpyxl")
    return {
        normalized(value).upper()
        for frame in sheets.values()
        if "Course Code" in frame
        for value in frame["Course Code"]
        if normalized(value)
    }


def read_exclusions(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows or set(rows[0]) != {"course_code"}:
        raise ValueError(f"{path} must have a course_code column")
    exclusions = {normalized(row["course_code"]).upper() for row in rows}
    if "" in exclusions:
        raise ValueError(f"{path} contains a blank course_code")
    return exclusions


def read_catalog_programs(curriculum: Path) -> dict[str, str]:
    programs: dict[str, set[str]] = {}
    with curriculum.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(line for line in source if not line.startswith("#")))
    if not rows or {"subject_code", "program_code"} - set(rows[0]):
        raise ValueError(f"{curriculum} must have subject_code and program_code columns")
    for row in rows:
        subject_code = normalized(row["subject_code"]).upper()
        program_code = normalized(row["program_code"]).upper()
        if not subject_code or program_code not in PROGRAM_CODES:
            raise ValueError(f"invalid curriculum row: {row!r}")
        programs.setdefault(subject_code, set()).add(program_code.removeprefix("BS"))
    return {code: "|".join(sorted(values)) for code, values in programs.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--excluded-course-codes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    exclusions = read_exclusions(args.excluded_course_codes)
    catalog_programs = read_catalog_programs(args.curriculum)
    rows: list[dict[str, str]] = []
    for code in sorted(read_course_codes(args.workbook)):
        if code in exclusions:
            continue
        programs = catalog_programs.get(code)
        if programs:
            rows.append({"course_code": code, "programs": programs})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=("course_code", "programs"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
