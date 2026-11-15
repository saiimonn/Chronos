"""CHR-98 / CHR-COLAB-002 workbook preprocessing tests."""

import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd


SCRIPT = (
    Path(__file__).parents[3]
    / "scripts"
    / "colab_prototype"
    / "preprocess_historical_dataset.py"
)
SPEC = importlib.util.spec_from_file_location("colab_preprocessing", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preprocessing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preprocessing
SPEC.loader.exec_module(preprocessing)

SCOPE_SCRIPT = (
    Path(__file__).parents[3]
    / "scripts"
    / "colab_prototype"
    / "build_scope_mapping.py"
)
SCOPE_SPEC = importlib.util.spec_from_file_location("colab_scope_mapping", SCOPE_SCRIPT)
assert SCOPE_SPEC is not None and SCOPE_SPEC.loader is not None
scope_builder = importlib.util.module_from_spec(SCOPE_SPEC)
sys.modules[SCOPE_SPEC.name] = scope_builder
SCOPE_SPEC.loader.exec_module(scope_builder)

HISTORICAL_CODE_CROSSWALK = SCRIPT.parent / "historical_code_crosswalk.csv"


def write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def write_workbook(
    path: Path, periods: list[str] | None = None, duplicate_primary: bool = False
) -> None:
    columns = list(preprocessing.REQUIRED_COLUMNS)
    primary = {
        "Group Number": "1",
        "Course Code": "CS 100",
        "Description": "Course",
        "Academic Units": "3",
        "Teacher/s": "Smith, Alex",
        "Schedule": "TTh 08:00 AM - 09:30 AM LB101",
    }
    continuation = {"Schedule": "Sat 08:00 AM - 09:30 AM LB102"}
    unknown = primary | {
        "Course Code": "MATH 100",
        "Schedule": "M 08:00 AM - 09:30 AM LB103",
    }
    out_of_scope = primary | {
        "Course Code": "ICT 100",
        "Schedule": "W 08:00 AM - 09:30 AM LB104",
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for index in range(4):
            current_primary = primary | {
                "Academic Period - Year": (periods or [
                    "1ST Semester - 2018",
                    "1ST Semester - 2019",
                    "1ST Semester - 2020",
                    "1ST Semester - 2021",
                ])[index]
            }
            rows = (
                [
                    current_primary,
                    continuation,
                    current_primary | unknown,
                    current_primary | out_of_scope,
                ]
                if index == 0
                else [current_primary]
            )
            if duplicate_primary and index == 0:
                rows.append(current_primary)
            pd.DataFrame(rows, columns=columns).to_excel(
                writer, sheet_name=f"S{index + 1}", index=False
            )


def test_preprocessing_reconstructs_continuations_and_quarantines_unmapped_scope(
    tmp_path: Path,
):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    write_workbook(workbook)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS|IT"])

    records, ledger, details = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert len(records) == 5
    meetings = {record["meeting_days"]: record for record in records}
    assert meetings["TTh"]["offering_source_row"] == 2
    assert meetings["Sa"]["offering_source_row"] == 2
    assert meetings["Sa"]["in_scope_programs"] == "CS|IT"
    assert details["accepted_record_count"] == 5
    assert [(row.reason_code, row.source_sheet) for row in ledger] == [
        ("UNMAPPED_COURSE_SCOPE", "S1"),
        ("OUT_OF_SCOPE_COURSE", "S1"),
    ]


def test_preprocessing_reserves_latest_three_whole_terms(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    write_workbook(
        workbook,
        [
            "1ST Semester - 2018",
            "1ST Semester - 2019",
            "1ST Semester - 2023",
            "2ND Semester - 2023",
        ],
    )
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2023-2024,1,true,true,",
            "S4,2023-2024,2,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])

    records, _, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )
    training, holdout, periods = preprocessing.split_holdout(
        records, preprocessing.load_term_manifest(manifest)
    )

    assert {(item["academic_year"], item["term_number"]) for item in training} == {
        ("2018-2019", 1)
    }
    assert periods == [("2019-2020", 1), ("2023-2024", 1), ("2023-2024", 2)]
    assert {(item["academic_year"], item["term_number"]) for item in holdout} == set(
        periods
    )
    assert training[0]["teacher_subject_eligible"] == 1
    assert type(training[0]["teacher_subject_eligible"]) is int
    assert training[0]["full_stream_eligible"] == 0
    assert type(training[0]["full_stream_eligible"]) is int


def test_title_crosswalk_normalizes_a_course_without_using_its_source_code(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    crosswalk = tmp_path / "crosswalk.csv"
    write_workbook(workbook)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])
    write_csv(crosswalk, "source_title,canonical_course_code,programs", ["Course,CIS 1101,CS|IT|IS"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
        preprocessing.load_title_crosswalk(crosswalk),
        excluded_course_codes=frozenset({"MATH 100"}),
    )

    assert {record["course_code"] for record in records} == {"CIS 1101"}
    assert {record["source_course_code"] for record in records} == {"CS 100"}
    assert [(row.reason_code, row.detail) for row in ledger] == [
        ("OUT_OF_SCOPE_COURSE", "MATH 100"),
        ("OUT_OF_SCOPE_COURSE", "ICT 100")
    ]


def test_code_crosswalk_retains_approved_it_source_codes(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    crosswalk = tmp_path / "crosswalk.csv"
    columns = list(preprocessing.REQUIRED_COLUMNS)
    approved = {
        "Group Number": "1",
        "Course Code": "IT 5101",
        "Description": "Business Analytics for IT",
        "Academic Units": "3",
        "Teacher/s": "Smith, Alex",
        "Schedule": "TTh 08:00 AM - 09:30 AM LB101",
    }
    excluded = approved | {"Course Code": "IT 9999"}
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for index in range(4):
            period = f"1ST Semester - {2018 + index}"
            pd.DataFrame(
                [
                    approved | {"Academic Period - Year": period},
                    excluded | {"Academic Period - Year": period},
                ],
                columns=columns,
            ).to_excel(
                writer, sheet_name=f"S{index + 1}", index=False
            )
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])
    write_csv(crosswalk, "source_course_code,equivalent_course_code,programs", ["IT 5101,IT 5101N,IT"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
        code_crosswalk=preprocessing.load_code_crosswalk(crosswalk),
    )

    assert {record["course_code"] for record in records} == {"IT 5101"}
    assert {record["canonical_course_code"] for record in records} == {"IT 5101N"}
    assert {record["equivalent_course_code"] for record in records} == {"IT 5101N"}
    assert [(row.reason_code, row.detail) for row in ledger] == [
        ("UNMAPPED_COURSE_SCOPE", "IT 9999")
    ] * 4


def test_is_1201_crosswalk_uses_cis_1201_membership_for_all_programs():
    crosswalk = preprocessing.load_code_crosswalk(HISTORICAL_CODE_CROSSWALK)

    assert crosswalk["IS 1201"].equivalent_course_code == "CIS 1201"
    assert crosswalk["IS 1201"].programs == ("CS", "IS", "IT")


def test_preprocessing_excludes_dissolved_courses(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    columns = list(preprocessing.REQUIRED_COLUMNS)
    dissolved = {
        "Group Number": "1",
        "Course Code": "CS 100",
        "Description": "Course",
        "Academic Units": "3",
        "Teacher/s": "Smith, Alex",
        "Course Status": "Dissolved",
        "Schedule": "TTh 08:00 AM - 09:30 AM LB101",
    }
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for index in range(4):
            pd.DataFrame(
                [dissolved | {"Academic Period - Year": f"1ST Semester - {2018 + index}"}],
                columns=columns,
            ).to_excel(
                writer, sheet_name=f"S{index + 1}", index=False
            )
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert records == []
    assert [(row.reason_code, row.detail) for row in ledger] == [
        ("DISSOLVED_COURSE", "CS 100")
    ] * 4


def test_scope_builder_uses_curriculum_membership_and_skips_cover_sheets(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    curriculum = tmp_path / "curriculum.csv"
    columns = list(preprocessing.REQUIRED_COLUMNS)
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame([{"Course Code": "CIS 2102"}, {"Course Code": "IT 3106"}]).to_excel(
            writer, sheet_name="Offerings", index=False
        )
        pd.DataFrame([{"Notes": "cover"}]).to_excel(writer, sheet_name="Cover", index=False)
    write_csv(
        curriculum,
        "program_code,effective_year,year_level,term_number,subject_code",
        [
            "BSCS,2023-2024,2,1,CIS 2102",
            "BSIS,2023-2024,2,1,CIS 2102",
            "BSIT,2023-2024,3,1,IT 3106",
        ],
    )

    assert scope_builder.read_course_codes(workbook) == {"CIS 2102", "IT 3106"}
    assert scope_builder.read_catalog_programs(curriculum) == {
        "CIS 2102": "CS|IS",
        "IT 3106": "IT",
    }


def test_preprocessing_skips_manifest_excluded_cover_sheet(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    columns = list(preprocessing.REQUIRED_COLUMNS)
    offering = {
        "Group Number": "1",
        "Course Code": "IT 3106",
        "Description": "IT Course",
        "Academic Units": "3",
        "Teacher/s": "Smith, Alex",
        "Schedule": "FSat 08:00 AM - 09:30 AM LB101",
    }
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for index in range(4):
            pd.DataFrame(
                [offering | {"Academic Period - Year": f"1ST Semester - {2018 + index}"}],
                columns=columns,
            ).to_excel(
                writer, sheet_name=f"S{index + 1}", index=False
            )
        pd.DataFrame([{"Notes": "cover"}]).to_excel(writer, sheet_name="Cover", index=False)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
            "Cover,,,false,false,workbook cover sheet",
        ],
    )
    write_csv(scope, "course_code,programs", ["IT 3106,IT"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert len(records) == 4
    assert {record["meeting_days"] for record in records} == {"FSa"}
    assert ledger == []


def test_holdout_does_not_substitute_an_older_term_when_latest_term_has_no_records():
    manifest = {
        f"S{index}": preprocessing.TermManifestRow(
            f"S{index}", f"202{index}-202{index + 1}", 1, True, True, ""
        )
        for index in range(1, 6)
    }
    records = [
        {"academic_year": f"202{index}-202{index + 1}", "term_number": 1}
        for index in range(1, 5)
    ]

    try:
        preprocessing.split_holdout(records, manifest)
    except ValueError as exc:
        assert "holdout term" in str(exc)
    else:
        raise AssertionError("an empty newest holdout term must not be replaced")


def test_canonical_days_accepts_documented_case_insensitive_aliases():
    assert preprocessing.canonical_days("FSat") == "FSa"
    assert preprocessing.canonical_days("tth") == "TTh"
    assert preprocessing.canonical_days("SUN") == "Su"


def test_preprocessing_rejects_source_period_that_disagrees_with_its_manifest(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    write_workbook(workbook)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2019-2020,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert len(records) == 3
    assert ledger[0].reason_code == "SOURCE_PERIOD_MANIFEST_MISMATCH"


def test_preprocessing_accepts_calendar_year_second_semester_source_labels(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    write_workbook(
        workbook,
        [
            "1ST Semester - 2018",
            "1ST Semester - 2019",
            "1ST Semester - 2020",
            "2ND Semester - 2022",
        ],
    )
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,2,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert len(records) == 5
    assert ledger == [
        preprocessing.LedgerRow("S1", 4, "UNMAPPED_COURSE_SCOPE", "MATH 100"),
        preprocessing.LedgerRow("S1", 5, "OUT_OF_SCOPE_COURSE", "ICT 100"),
    ]


def test_title_crosswalk_can_require_an_approved_source_course_code(tmp_path: Path):
    crosswalk = tmp_path / "crosswalk.csv"
    write_csv(
        crosswalk,
        "source_title,canonical_course_code,programs,source_course_codes",
        ["Security,IT 3104N,IT,IT 3104"],
    )

    mapping = preprocessing.load_title_crosswalk(crosswalk)

    assert mapping["SECURITY"].source_course_codes == frozenset({"IT 3104"})


def test_title_crosswalk_does_not_apply_to_an_unapproved_source_course(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    crosswalk = tmp_path / "crosswalk.csv"
    write_workbook(workbook)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])
    write_csv(
        crosswalk,
        "source_title,canonical_course_code,programs,source_course_codes",
        ["Course,IT 3104N,IT,IT 3104"],
    )

    records, _, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
        preprocessing.load_title_crosswalk(crosswalk),
    )

    assert {record["canonical_course_code"] for record in records} == {"CS 100"}


def test_preprocessing_removes_content_duplicates(tmp_path: Path):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    write_workbook(workbook, duplicate_primary=True)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])

    records, ledger, _ = preprocessing.process_workbook(
        workbook,
        preprocessing.load_term_manifest(manifest),
        preprocessing.load_scope_mapping(scope),
    )

    assert len(records) == 5
    assert [item.reason_code for item in ledger].count("LOGICAL_DUPLICATE") == 1


def test_output_swap_refuses_unmanaged_existing_files(tmp_path: Path):
    staging_dir = tmp_path / "staging"
    output_dir = tmp_path / "output"
    staging_dir.mkdir()
    (staging_dir / "canonical_records.csv").write_text("header\n", encoding="utf-8")
    output_dir.mkdir()
    sentinel = output_dir / "do-not-delete.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    try:
        preprocessing.replace_output_directory(staging_dir, output_dir)
    except ValueError as exc:
        assert "not managed" in str(exc)
    else:
        raise AssertionError("unmanaged output files must prevent replacement")

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_cli_writes_reproducible_input_and_output_provenance(
    tmp_path: Path, monkeypatch
):
    workbook = tmp_path / "source.xlsx"
    manifest = tmp_path / "terms.csv"
    scope = tmp_path / "scope.csv"
    output_dir = tmp_path / "output"
    write_workbook(workbook)
    write_csv(
        manifest,
        "source_sheet,academic_year,term_number,complete,include_in_research,exclusion_reason",
        [
            "S1,2018-2019,1,true,true,",
            "S2,2019-2020,1,true,true,",
            "S3,2020-2021,1,true,true,",
            "S4,2021-2022,1,true,true,",
        ],
    )
    write_csv(scope, "course_code,programs", ["CS 100,CS"])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preprocess_historical_dataset.py",
            "--workbook",
            str(workbook),
            "--term-manifest",
            str(manifest),
            "--scope-mapping",
            str(scope),
            "--output-dir",
            str(output_dir),
        ],
    )

    preprocessing.main()

    details = json.loads((output_dir / "source_manifest.json").read_text())
    assert set(details["input_sha256"]) == {
        "workbook",
        "term_manifest",
        "scope_mapping",
        "title_crosswalk",
        "code_crosswalk",
        "excluded_course_codes",
        "preprocessor",
    }
    assert set(details["output_sha256"]) == {
        "canonical_records.csv",
        "training_records.csv",
        "holdout_records.csv",
        "rejection_ledger.csv",
    }
    assert all(
        b"\r\n" not in (output_dir / filename).read_bytes()
        for filename in details["output_sha256"]
    )
