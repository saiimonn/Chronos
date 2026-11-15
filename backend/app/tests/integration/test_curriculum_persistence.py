import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    CourseProgram,
    Curriculum,
    CurriculumRequirement,
    Subject,
    Term,
)
from app.services.catalog.curriculum import (
    NoApplicableCurriculumError,
    required_subject_codes,
    resolve_for_block,
    resolve_for_cohort,
)

pytestmark = pytest.mark.postgres


def make_program(db: Session, program_code: str = "BSCS") -> CourseProgram:
    program = CourseProgram(
        program_code=program_code,
        program_name="Bachelor of Science in Computer Science",
    )
    db.add(program)
    db.flush()
    return program


def make_subject(db: Session, subject_code: str, subject_type: str = "LEC") -> Subject:
    subject = Subject(
        subject_code=subject_code,
        subject_title=f"Subject {subject_code}",
        units=3,
        subject_type=subject_type,
    )
    db.add(subject)
    db.flush()
    return subject


def make_curriculum(
    db: Session, program_code: str, effective_year: str
) -> Curriculum:
    curriculum = Curriculum(
        program_code=program_code, effective_year=effective_year
    )
    db.add(curriculum)
    db.flush()
    return curriculum


def make_requirement(
    db: Session,
    curriculum: Curriculum,
    subject_code: str,
    year_level: int = 1,
    term_number: int = 1,
) -> CurriculumRequirement:
    requirement = CurriculumRequirement(
        curriculum_id=curriculum.curriculum_id,
        year_level=year_level,
        term_number=term_number,
        subject_code=subject_code,
    )
    db.add(requirement)
    db.flush()
    return requirement


def test_curriculum_and_requirements_persist(db_session: Session):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    requirement = make_requirement(db_session, curriculum, "CIS 1101")

    assert curriculum.curriculum_id is not None
    assert curriculum.label == "BSCS 2023-2024"
    assert curriculum.requirements == [requirement]
    assert requirement.curriculum is curriculum


def test_duplicate_effective_year_per_program_is_rejected(db_session: Session):
    make_program(db_session)
    make_curriculum(db_session, "BSCS", "2023-2024")
    db_session.add(Curriculum(program_code="BSCS", effective_year="2023-2024"))
    with pytest.raises(IntegrityError, match="uq_curriculum_program_effective_year"):
        db_session.flush()


def test_same_effective_year_across_programs_is_allowed(db_session: Session):
    make_program(db_session, "BSCS")
    make_program(db_session, "BSIT")
    make_curriculum(db_session, "BSCS", "2023-2024")
    make_curriculum(db_session, "BSIT", "2023-2024")

    stored = db_session.scalars(select(Curriculum)).all()
    assert {row.program_code for row in stored} == {"BSCS", "BSIT"}


def test_malformed_effective_year_is_rejected(db_session: Session):
    make_program(db_session)
    db_session.add(Curriculum(program_code="BSCS", effective_year="2023"))
    with pytest.raises(IntegrityError, match="ck_curriculum_effective_year_format"):
        db_session.flush()


@pytest.mark.parametrize("year_level", [0, 6])
def test_year_level_out_of_range_is_rejected(db_session: Session, year_level: int):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    db_session.add(
        CurriculumRequirement(
            curriculum_id=curriculum.curriculum_id,
            year_level=year_level,
            term_number=1,
            subject_code="CIS 1101",
        )
    )
    with pytest.raises(
        IntegrityError, match="ck_curriculum_requirement_year_level_range"
    ):
        db_session.flush()


@pytest.mark.parametrize("term_number", [0, 4])
def test_term_number_out_of_range_is_rejected(db_session: Session, term_number: int):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    db_session.add(
        CurriculumRequirement(
            curriculum_id=curriculum.curriculum_id,
            year_level=1,
            term_number=term_number,
            subject_code="CIS 1101",
        )
    )
    with pytest.raises(
        IntegrityError, match="ck_curriculum_requirement_term_number_range"
    ):
        db_session.flush()


def test_duplicate_placement_is_rejected(db_session: Session):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    make_requirement(db_session, curriculum, "CIS 1101")
    db_session.add(
        CurriculumRequirement(
            curriculum_id=curriculum.curriculum_id,
            year_level=1,
            term_number=1,
            subject_code="CIS 1101",
        )
    )
    with pytest.raises(IntegrityError, match="uq_curriculum_requirement_placement"):
        db_session.flush()


def test_same_subject_in_a_different_term_is_allowed(db_session: Session):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    make_requirement(db_session, curriculum, "CIS 1101", term_number=1)
    make_requirement(db_session, curriculum, "CIS 1101", term_number=2)

    assert len(curriculum.requirements) == 2


def test_deleting_a_curriculum_cascades_to_requirements(db_session: Session):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    make_requirement(db_session, curriculum, "CIS 1101")

    db_session.delete(curriculum)
    db_session.flush()

    assert db_session.scalars(select(CurriculumRequirement)).all() == []


def test_deleting_a_referenced_subject_is_restricted(db_session: Session):
    make_program(db_session)
    subject = make_subject(db_session, "CIS 1101")
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    make_requirement(db_session, curriculum, "CIS 1101")

    db_session.delete(subject)
    with pytest.raises(IntegrityError, match="fk_curriculum_requirement_subject_code"):
        db_session.flush()


def test_resolution_prefers_the_exact_entry_year(db_session: Session):
    make_program(db_session)
    make_curriculum(db_session, "BSCS", "2019-2020")
    expected = make_curriculum(db_session, "BSCS", "2023-2024")

    # A second-year cohort in 2024-2025 entered in 2023-2024.
    resolved = resolve_for_cohort(db_session, "BSCS", 2, "2024-2025")
    assert resolved.curriculum_id == expected.curriculum_id


def test_resolution_falls_back_to_the_most_recent_earlier_year(db_session: Session):
    make_program(db_session)
    make_curriculum(db_session, "BSCS", "2019-2020")
    expected = make_curriculum(db_session, "BSCS", "2023-2024")

    # A first-year cohort in 2025-2026 entered in 2025-2026, which has no row.
    resolved = resolve_for_cohort(db_session, "BSCS", 1, "2025-2026")
    assert resolved.curriculum_id == expected.curriculum_id


def test_resolution_ignores_curricula_from_another_program(db_session: Session):
    make_program(db_session, "BSCS")
    make_program(db_session, "BSIT")
    make_curriculum(db_session, "BSIT", "2023-2024")

    with pytest.raises(NoApplicableCurriculumError):
        resolve_for_cohort(db_session, "BSCS", 1, "2023-2024")


def test_resolution_raises_when_the_cohort_predates_every_curriculum(
    db_session: Session,
):
    make_program(db_session)
    make_curriculum(db_session, "BSCS", "2023-2024")

    with pytest.raises(NoApplicableCurriculumError) as excinfo:
        resolve_for_cohort(db_session, "BSCS", 5, "2023-2024")

    assert excinfo.value.program_code == "BSCS"
    assert excinfo.value.entry_year == "2019-2020"


def test_resolution_reads_a_block_through_its_term(db_session: Session):
    make_program(db_session)
    expected = make_curriculum(db_session, "BSCS", "2023-2024")
    term = Term(academic_year="2024-2025", term_number=1, is_active=False)
    db_session.add(term)
    db_session.flush()
    block = BlockSection(
        term_id=term.term_id,
        program_code="BSCS",
        year_level=2,
        section_letter="A",
    )
    db_session.add(block)
    db_session.flush()

    resolved = resolve_for_block(db_session, block)
    assert resolved.curriculum_id == expected.curriculum_id


def test_required_subjects_are_scoped_to_year_level_and_term(db_session: Session):
    make_program(db_session)
    for code in ("CIS 1101", "CIS 1102", "CIS 2101"):
        make_subject(db_session, code)
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")
    make_requirement(db_session, curriculum, "CIS 1101", year_level=1, term_number=1)
    make_requirement(db_session, curriculum, "CIS 1102", year_level=1, term_number=1)
    make_requirement(db_session, curriculum, "CIS 2101", year_level=2, term_number=1)

    assert required_subject_codes(db_session, curriculum, 1, 1) == [
        "CIS 1101",
        "CIS 1102",
    ]
    assert required_subject_codes(db_session, curriculum, 2, 1) == ["CIS 2101"]


def test_a_curriculum_recording_nothing_for_a_term_resolves_to_an_empty_list(
    db_session: Session,
):
    make_program(db_session)
    curriculum = make_curriculum(db_session, "BSCS", "2023-2024")

    # Empty is a real answer, distinct from the curriculum being missing.
    assert required_subject_codes(db_session, curriculum, 1, 1) == []


def test_requirement_needs_an_existing_curriculum(db_session: Session):
    make_program(db_session)
    make_subject(db_session, "CIS 1101")
    db_session.add(
        CurriculumRequirement(
            curriculum_id=uuid.uuid4(),
            year_level=1,
            term_number=1,
            subject_code="CIS 1101",
        )
    )
    with pytest.raises(
        IntegrityError, match="fk_curriculum_requirement_curriculum_id"
    ):
        db_session.flush()
