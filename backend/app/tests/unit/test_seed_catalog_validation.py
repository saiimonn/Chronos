from pathlib import Path

import pytest

from app.cli.seed_catalog import (
    CURRICULUM_FILE,
    PROGRAMS_FILE,
    SUBJECTS_FILE,
    SeedDataError,
    parse_rows,
    read_rows,
    validate,
)

PROGRAM_COLUMNS = ("program_code", "program_name")
SUBJECT_COLUMNS = ("subject_code", "subject_title", "units", "subject_type")
CURRICULUM_COLUMNS = (
    "program_code",
    "effective_year",
    "year_level",
    "term_number",
    "subject_code",
)

GOOD_PROGRAMS = """\
program_code,program_name
BSCS,Bachelor of Science in Computer Science
"""

GOOD_SUBJECTS = """\
subject_code,subject_title,units,subject_type
CIS 1101,Introduction to Computing,3,LEC
CIS 1101L,Introduction to Computing (Laboratory),1,LAB
"""

GOOD_CURRICULUM = """\
program_code,effective_year,year_level,term_number,subject_code
BSCS,2023-2024,1,1,CIS 1101
BSCS,2023-2024,1,1,CIS 1101L
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def rows_for(tmp_path: Path, programs=None, subjects=None, curriculum=None):
    return (
        read_rows(
            write(tmp_path, PROGRAMS_FILE, programs or GOOD_PROGRAMS), PROGRAM_COLUMNS
        ),
        read_rows(
            write(tmp_path, SUBJECTS_FILE, subjects or GOOD_SUBJECTS), SUBJECT_COLUMNS
        ),
        read_rows(
            write(tmp_path, CURRICULUM_FILE, curriculum or GOOD_CURRICULUM),
            CURRICULUM_COLUMNS,
        ),
    )


def violations_of(tmp_path: Path, **overrides) -> list[str]:
    with pytest.raises(SeedDataError) as excinfo:
        validate(*rows_for(tmp_path, **overrides))
    return excinfo.value.violations


def test_valid_files_pass(tmp_path: Path):
    validate(*rows_for(tmp_path))


def test_comments_and_blank_lines_are_skipped(tmp_path: Path):
    text = """\
# a leading comment
# another one

program_code,program_name

BSCS,Bachelor of Science in Computer Science
   # an indented comment
BSIT,Bachelor of Science in Information Technology
"""
    rows = read_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS)
    assert [row.get("program_code") for row in rows] == ["BSCS", "BSIT"]


def test_line_numbers_survive_comment_stripping(tmp_path: Path):
    # The reported line must be the line in the file, not the index among the
    # rows that survived filtering, or a correction lands in the wrong place.
    text = """\
# comment
program_code,program_name

BSCS,Bachelor of Science in Computer Science
"""
    rows = read_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS)
    assert rows[0].line == 4


def test_missing_file_is_reported(tmp_path: Path):
    with pytest.raises(SeedDataError) as excinfo:
        read_rows(tmp_path / "absent.csv", PROGRAM_COLUMNS)
    assert any("file not found" in text for text in excinfo.value.violations)


def test_wrong_header_is_reported(tmp_path: Path):
    text = "program_code,name\nBSCS,Computer Science\n"
    with pytest.raises(SeedDataError) as excinfo:
        read_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS)
    assert any("header is" in text for text in excinfo.value.violations)


def test_wrong_column_count_is_reported(tmp_path: Path):
    text = "program_code,program_name\nBSCS\n"
    with pytest.raises(SeedDataError) as excinfo:
        read_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS)
    assert any("expected 2" in text for text in excinfo.value.violations)


def test_every_malformed_row_in_a_file_is_reported(tmp_path: Path):
    # A short row must not hide the next one: the whole file is read and every
    # structural fault is listed with its line.
    text = (
        "program_code,program_name\n"
        "BSCS\n"
        "BSIT,Information Technology\n"
        "BSIS,Information Systems,extra\n"
    )
    with pytest.raises(SeedDataError) as excinfo:
        read_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS)
    assert excinfo.value.violations == [
        f"{PROGRAMS_FILE}:2: has 1 column(s), expected 2",
        f"{PROGRAMS_FILE}:4: has 3 column(s), expected 2",
    ]


def test_well_formed_rows_survive_a_malformed_neighbour(tmp_path: Path):
    text = "program_code,program_name\nBSCS\nBSIT,Information Technology\n"
    violations: list[str] = []
    rows = parse_rows(write(tmp_path, PROGRAMS_FILE, text), PROGRAM_COLUMNS, violations)
    assert [row.get("program_code") for row in rows] == ["BSIT"]
    assert len(violations) == 1


def test_unknown_subject_code_is_rejected(tmp_path: Path):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 9999\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("unknown subject_code 'CIS 9999'" in text for text in violations)
    assert any(f"{CURRICULUM_FILE}:2" in text for text in violations)


def test_unknown_program_code_is_rejected(tmp_path: Path):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSIS,2023-2024,1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("unknown program_code 'BSIS'" in text for text in violations)


@pytest.mark.parametrize("subject_type", ["lec", "LECTURE", "BOTH", ""])
def test_subject_type_must_be_lec_or_lab(tmp_path: Path, subject_type: str):
    subjects = (
        "subject_code,subject_title,units,subject_type\n"
        f"CIS 1101,Introduction to Computing,3,{subject_type}\n"
    )
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, subjects=subjects, curriculum=curriculum)
    assert any("subject_type" in text for text in violations)


@pytest.mark.parametrize("units", ["0", "-1", "three", "3.5", ""])
def test_units_must_be_a_positive_integer(tmp_path: Path, units: str):
    subjects = (
        "subject_code,subject_title,units,subject_type\n"
        f"CIS 1101,Introduction to Computing,{units},LEC\n"
    )
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, subjects=subjects, curriculum=curriculum)
    assert any("units" in text for text in violations)


@pytest.mark.parametrize("effective_year", ["2023", "2023/2024", "2023-2025", ""])
def test_effective_year_must_be_a_consecutive_pair(
    tmp_path: Path, effective_year: str
):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        f"BSCS,{effective_year},1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("academic year" in text for text in violations)


@pytest.mark.parametrize("year_level", ["0", "6", "one", ""])
def test_year_level_must_be_in_range(tmp_path: Path, year_level: str):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        f"BSCS,2023-2024,{year_level},1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("year_level" in text for text in violations)


@pytest.mark.parametrize("term_number", ["0", "4", "first", ""])
def test_term_number_must_be_in_range(tmp_path: Path, term_number: str):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        f"BSCS,2023-2024,1,{term_number},CIS 1101\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("term_number" in text for text in violations)


def test_duplicate_subject_code_is_rejected(tmp_path: Path):
    subjects = (
        "subject_code,subject_title,units,subject_type\n"
        "CIS 1101,Introduction to Computing,3,LEC\n"
        "CIS 1101,Introduction to Computing Again,3,LEC\n"
    )
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, subjects=subjects, curriculum=curriculum)
    assert any("duplicate subject_code" in text for text in violations)
    assert any("already given on line 2" in text for text in violations)


def test_duplicate_requirement_is_rejected(tmp_path: Path):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert any("duplicate requirement" in text for text in violations)


def test_the_same_subject_in_another_term_is_not_a_duplicate(tmp_path: Path):
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023-2024,1,1,CIS 1101\n"
        "BSCS,2023-2024,1,2,CIS 1101\n"
    )
    validate(*rows_for(tmp_path, curriculum=curriculum))


def test_every_violation_is_reported_in_one_pass(tmp_path: Path):
    # Someone fixing a hand-built CSV should see the whole list, not discover
    # the next fault after each correction.
    curriculum = (
        "program_code,effective_year,year_level,term_number,subject_code\n"
        "BSCS,2023,9,7,CIS 9999\n"
    )
    violations = violations_of(tmp_path, curriculum=curriculum)
    assert len(violations) == 4


def test_blank_required_value_is_rejected(tmp_path: Path):
    programs = "program_code,program_name\nBSCS,\n"
    violations = violations_of(tmp_path, programs=programs)
    assert any("program_name is blank" in text for text in violations)


def test_oversized_value_is_rejected(tmp_path: Path):
    programs = f"program_code,program_name\n{'X' * 21},A Program\n"
    violations = violations_of(tmp_path, programs=programs)
    assert any("maximum is 20" in text for text in violations)


def test_shipped_templates_are_valid(tmp_path: Path):
    # The committed CSVs must pass their own validator, or the first run of a
    # fresh installation fails on data this repository shipped.
    data_dir = Path(__file__).resolve().parents[3] / "data"
    validate(
        read_rows(data_dir / PROGRAMS_FILE, PROGRAM_COLUMNS),
        read_rows(data_dir / SUBJECTS_FILE, SUBJECT_COLUMNS),
        read_rows(data_dir / CURRICULUM_FILE, CURRICULUM_COLUMNS),
    )


# Subjects that need a scheduling decision each term. The administrator adds
# them by hand, so automatic cohort generation must never see them.
MANUAL_ONLY_SUBJECTS = {"CS 4102N", "IT 4102N", "IT 4202N", "IS 4102", "IS 4202"}


def test_shipped_seed_leaves_manual_only_subjects_out():
    data_dir = Path(__file__).resolve().parents[3] / "data"
    subjects = {
        row.get("subject_code")
        for row in read_rows(data_dir / SUBJECTS_FILE, SUBJECT_COLUMNS)
    }
    required = {
        row.get("subject_code")
        for row in read_rows(data_dir / CURRICULUM_FILE, CURRICULUM_COLUMNS)
    }
    assert not subjects & MANUAL_ONLY_SUBJECTS
    assert not required & MANUAL_ONLY_SUBJECTS
