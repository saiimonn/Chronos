from uuid import UUID

from app.services.curriculum_readiness import (
    CohortCoverage,
    analyze_curriculum_coverage,
)

BLOCK_ID = UUID("00000000-0000-0000-0000-0000000000b1")


def coverage(
    required: frozenset[str] | None, taken: frozenset[str] = frozenset()
) -> CohortCoverage:
    return CohortCoverage(
        block_id=BLOCK_ID,
        block_name="BSCS-1A",
        program_code="BSCS",
        academic_year="2026-2027",
        term_number=1,
        entry_year="2026-2027",
        required_subject_codes=required,
        taken_subject_codes=taken,
    )


def test_a_block_taking_every_required_subject_has_no_warning():
    cohort = coverage(
        frozenset({"CIS 1101", "CIS 1102"}), frozenset({"CIS 1101", "CIS 1102"})
    )

    assert analyze_curriculum_coverage([cohort]) == ()


def test_a_block_missing_required_subjects_names_every_one():
    cohort = coverage(
        frozenset({"CIS 1103", "CIS 1101", "CIS 1102"}), frozenset({"CIS 1101"})
    )

    (warning,) = analyze_curriculum_coverage([cohort])

    assert warning.code == "COHORT_MISSING_REQUIRED_SUBJECT"
    assert warning.details == {
        "block_id": str(BLOCK_ID),
        "block_name": "BSCS-1A",
        "missing_subject_codes": ["CIS 1102", "CIS 1103"],
    }
    # The frontend renders only the message, so it must carry the specifics.
    assert "BSCS-1A" in warning.message
    assert "CIS 1102, CIS 1103" in warning.message


def test_an_unresolved_curriculum_is_not_reported_as_missing_subjects():
    (warning,) = analyze_curriculum_coverage([coverage(None)])

    assert warning.code == "COHORT_WITHOUT_CURRICULUM"
    assert warning.details == {
        "block_id": str(BLOCK_ID),
        "block_name": "BSCS-1A",
        "program_code": "BSCS",
        "entry_year": "2026-2027",
    }


def test_an_unreadable_term_year_is_reported_as_a_term_fault():
    cohort = CohortCoverage(
        block_id=BLOCK_ID,
        block_name="BSCS-1A",
        program_code="BSCS",
        academic_year="2026",
        term_number=1,
        entry_year=None,
        required_subject_codes=None,
        taken_subject_codes=frozenset(),
    )

    (warning,) = analyze_curriculum_coverage([cohort])

    assert warning.code == "COHORT_ENTRY_YEAR_UNREADABLE"
    assert warning.details == {
        "block_id": str(BLOCK_ID),
        "block_name": "BSCS-1A",
        "academic_year": "2026",
    }
    assert "'2026'" in warning.message


def test_a_term_outside_the_curriculum_range_is_not_read_as_requiring_nothing():
    cohort = CohortCoverage(
        block_id=BLOCK_ID,
        block_name="BSCS-1A",
        program_code="BSCS",
        academic_year="2026-2027",
        term_number=4,
        entry_year="2026-2027",
        required_subject_codes=None,
        taken_subject_codes=frozenset(),
    )

    (warning,) = analyze_curriculum_coverage([cohort])

    assert warning.code == "COHORT_TERM_OUTSIDE_CURRICULUM"
    assert warning.details == {
        "block_id": str(BLOCK_ID),
        "block_name": "BSCS-1A",
        "term_number": 4,
    }
    assert "term 4" in warning.message


def test_subjects_beyond_the_curriculum_never_warn():
    cohort = coverage(
        frozenset({"CIS 1101"}), frozenset({"CIS 1101", "ELEC 1"})
    )

    assert analyze_curriculum_coverage([cohort]) == ()


def test_a_curriculum_requiring_nothing_this_term_has_no_warning():
    assert analyze_curriculum_coverage([coverage(frozenset())]) == ()


def test_no_blocks_means_no_warnings():
    assert analyze_curriculum_coverage([]) == ()


def test_each_block_is_reported_on_its_own():
    first = coverage(frozenset({"CIS 1101"}))
    second = CohortCoverage(
        block_id=UUID("00000000-0000-0000-0000-0000000000b2"),
        block_name="BSIT-2A",
        program_code="BSIT",
        academic_year="2026-2027",
        term_number=1,
        entry_year="2025-2026",
        required_subject_codes=None,
        taken_subject_codes=frozenset(),
    )

    warnings = analyze_curriculum_coverage([first, second])

    assert [warning.code for warning in warnings] == [
        "COHORT_MISSING_REQUIRED_SUBJECT",
        "COHORT_WITHOUT_CURRICULUM",
    ]
