"""What the CHR-BLK-002 preflight refuses to migrate.

The detection is a pure function over rows so every defect class can be stated
here without a database: the command that reads those rows adds nothing but the
queries. Each case is a shape the cutover cannot resolve on its own — either
the mapping is undetermined, or the new constraints would reject the result
part-way through the migration.
"""

import uuid

import pytest

from app.domain import SubjectType
from app.services.migration.block_preflight import (
    Defect,
    DefectKind,
    LegacySection,
    find_defects,
)

TERM = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TERM = uuid.UUID("22222222-2222-2222-2222-222222222222")


def section(**overrides) -> LegacySection:
    values = {
        "section_id": uuid.uuid4(),
        "term_id": TERM,
        "subject_code": "CS101",
        "program_code": "BSCS",
        "year_level": 1,
        "section_letter": "A",
        "expected_enrollment": 20,
    }
    return LegacySection(**(values | overrides))


def find(sections, offering_counts=None, subject_types=None, schedule_counts=None):
    return find_defects(
        sections,
        offering_counts=offering_counts if offering_counts is not None else {},
        subject_types=(
            {"CS101": SubjectType.LEC} if subject_types is None else subject_types
        ),
        schedule_counts=schedule_counts or {},
    )


def kinds(defects: list[Defect]) -> list[DefectKind]:
    return [defect.kind for defect in defects]


def test_a_section_whose_subject_has_one_offering_is_clean() -> None:
    assert find([section()], offering_counts={(TERM, "CS101"): 1}) == []


def test_a_subject_with_no_offering_in_the_term_is_reported_as_fixable() -> None:
    defects = find([section()], offering_counts={})

    assert kinds(defects) == [DefectKind.MISSING_OFFERING]
    assert defects[0].subject_code == "CS101"
    assert defects[0].is_fixable


def test_an_offering_in_another_term_does_not_satisfy_the_section() -> None:
    defects = find([section()], offering_counts={(OTHER_TERM, "CS101"): 1})

    assert kinds(defects) == [DefectKind.MISSING_OFFERING]


def test_a_subject_with_two_offerings_is_ambiguous_and_not_fixable() -> None:
    defects = find([section()], offering_counts={(TERM, "CS101"): 2})

    assert kinds(defects) == [DefectKind.AMBIGUOUS_OFFERING]
    assert not defects[0].is_fixable


def test_a_lecture_taken_by_three_cohorts_exceeds_the_block_limit() -> None:
    sections = [
        section(section_letter=letter, section_id=uuid.uuid4())
        for letter in ("A", "B", "C")
    ]

    defects = find(sections, offering_counts={(TERM, "CS101"): 1})

    assert kinds(defects) == [DefectKind.BLOCK_LIMIT_EXCEEDED]
    assert "3" in defects[0].detail and "2" in defects[0].detail


def test_a_lecture_taken_by_two_cohorts_is_within_the_block_limit() -> None:
    sections = [section(section_letter=letter) for letter in ("A", "B")]

    assert find(sections, offering_counts={(TERM, "CS101"): 1}) == []


def test_a_lab_taken_by_two_cohorts_exceeds_the_block_limit() -> None:
    sections = [
        section(subject_code="CS101L", section_letter=letter) for letter in ("A", "B")
    ]

    defects = find(
        sections,
        offering_counts={(TERM, "CS101L"): 1},
        subject_types={"CS101L": SubjectType.LAB},
    )

    assert kinds(defects) == [DefectKind.BLOCK_LIMIT_EXCEEDED]


def test_the_block_limit_counts_cohorts_not_rows() -> None:
    # One cohort taking the same subject twice cannot happen under the old
    # unique constraint, but two subjects for one cohort must not read as two
    # Blocks either.
    sections = [section(), section(subject_code="CS102")]

    assert (
        find(
            sections,
            offering_counts={(TERM, "CS101"): 1, (TERM, "CS102"): 1},
            subject_types={"CS101": SubjectType.LEC, "CS102": SubjectType.LEC},
        )
        == []
    )


def test_enrollment_above_the_expanded_capacity_is_reported() -> None:
    defects = find(
        [section(expected_enrollment=26)], offering_counts={(TERM, "CS101"): 1}
    )

    assert kinds(defects) == [DefectKind.ENROLLMENT_ABOVE_CAPACITY]
    assert not defects[0].is_fixable


def test_enrollment_within_the_expanded_capacity_is_clean() -> None:
    assert (
        find([section(expected_enrollment=25)], offering_counts={(TERM, "CS101"): 1})
        == []
    )


def test_one_cohort_with_disagreeing_enrollments_is_reported() -> None:
    sections = [
        section(expected_enrollment=18),
        section(subject_code="CS102", expected_enrollment=24),
    ]

    defects = find(
        sections,
        offering_counts={(TERM, "CS101"): 1, (TERM, "CS102"): 1},
        subject_types={"CS101": SubjectType.LEC, "CS102": SubjectType.LEC},
    )

    assert kinds(defects) == [DefectKind.ENROLLMENT_DISAGREEMENT]


def test_section_letters_differing_only_by_case_collide_into_one_block() -> None:
    sections = [section(section_letter="A"), section(section_letter="a")]

    defects = find(sections, offering_counts={(TERM, "CS101"): 1})

    assert DefectKind.SECTION_LETTER_CASE_COLLISION in kinds(defects)


def test_a_defect_reports_how_many_schedule_records_depend_on_it() -> None:
    scheduled = section()
    unscheduled = section(subject_code="CS102")

    defects = find(
        [scheduled, unscheduled],
        offering_counts={},
        subject_types={"CS101": SubjectType.LEC, "CS102": SubjectType.LEC},
        schedule_counts={scheduled.section_id: 4},
    )

    by_subject = {defect.subject_code: defect for defect in defects}
    assert by_subject["CS101"].schedule_record_count == 4
    assert by_subject["CS102"].schedule_record_count == 0


def test_defects_are_ordered_deterministically() -> None:
    sections = [
        section(term_id=OTHER_TERM, subject_code="ZZ999"),
        section(subject_code="CS102"),
        section(),
    ]
    subject_types = dict.fromkeys(
        ("CS101", "CS102", "ZZ999"), SubjectType.LEC
    )

    defects = find(sections, offering_counts={}, subject_types=subject_types)

    assert [(defect.term_id, defect.subject_code) for defect in defects] == sorted(
        (defect.term_id, defect.subject_code) for defect in defects
    )


def test_an_unknown_subject_type_is_a_hard_error() -> None:
    with pytest.raises(KeyError):
        find([section()], offering_counts={(TERM, "CS101"): 1}, subject_types={})
