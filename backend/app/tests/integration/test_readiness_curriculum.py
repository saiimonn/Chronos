import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Curriculum,
    CurriculumRequirement,
    Subject,
    SubjectOffering,
    Term,
)
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

CURRICULUM_CODES = {
    "COHORT_ENTRY_YEAR_UNREADABLE",
    "COHORT_MISSING_REQUIRED_SUBJECT",
    "COHORT_TERM_OUTSIDE_CURRICULUM",
    "COHORT_WITHOUT_CURRICULUM",
}


def add(session: Session, row):
    session.add(row)
    session.flush()
    return row


def make_block(session: Session, term: Term, program_code: str, letter: str):
    return add(
        session,
        BlockSection(
            term_id=term.term_id,
            program_code=program_code,
            year_level=1,
            section_letter=letter,
        ),
    )


def take(session: Session, block: BlockSection, subject_code: str) -> None:
    offering = add(
        session,
        SubjectOffering(
            term_id=block.term_id,
            subject_code=subject_code,
            # Each Block gets its own offering, numbered after any other
            # Block's offering of the same subject.
            group_no=session.scalar(
                select(func.count()).where(
                    SubjectOffering.term_id == block.term_id,
                    SubjectOffering.subject_code == subject_code,
                )
            )
            + 1,
        ),
    )
    add(
        session,
        BlockSectionOffering(
            block_id=block.block_id,
            offering_id=offering.offering_id,
            term_id=block.term_id,
            subject_code=subject_code,
        ),
    )


def test_readiness_reports_each_block_against_its_curriculum(db_session: Session):
    term = add(
        db_session, Term(academic_year="2026-2027", term_number=1, is_active=True)
    )
    for program_code in ("BSCS", "BSIT"):
        add(
            db_session,
            CourseProgram(program_code=program_code, program_name=program_code),
        )
    for subject_code in ("CIS 1101", "CIS 1102", "CIS 1103", "ELEC 1"):
        add(
            db_session,
            Subject(
                subject_code=subject_code,
                subject_title=subject_code,
                units=3,
                subject_type="LEC",
            ),
        )
    # An earlier prospectus still governs a cohort entering in 2026-2027.
    curriculum = add(
        db_session, Curriculum(program_code="BSCS", effective_year="2023-2024")
    )
    for subject_code in ("CIS 1101", "CIS 1102", "CIS 1103"):
        add(
            db_session,
            CurriculumRequirement(
                curriculum_id=curriculum.curriculum_id,
                year_level=1,
                term_number=1,
                subject_code=subject_code,
            ),
        )
    # A requirement for another term must not leak into this one.
    add(
        db_session,
        CurriculumRequirement(
            curriculum_id=curriculum.curriculum_id,
            year_level=1,
            term_number=2,
            subject_code="ELEC 1",
        ),
    )

    complete = make_block(db_session, term, "BSCS", "A")
    for subject_code in ("CIS 1101", "CIS 1102", "CIS 1103", "ELEC 1"):
        take(db_session, complete, subject_code)
    incomplete = make_block(db_session, term, "BSCS", "B")
    take(db_session, incomplete, "CIS 1102")
    uncovered = make_block(db_session, term, "BSIT", "A")
    take(db_session, uncovered, "CIS 1101")

    client = make_api_client(db_session)
    response = client.get(f"/api/v1/terms/{term.term_id}/readiness")

    assert response.status_code == 200
    body = response.json()
    # A curriculum gap does not stop the GA, so it never counts as a blocker.
    assert not {blocker["code"] for blocker in body["blockers"]} & CURRICULUM_CODES
    assert body["warnings"] == [
        {
            "code": "COHORT_MISSING_REQUIRED_SUBJECT",
            "message": "BSCS-1B does not take required subjects: CIS 1101, CIS 1103.",
            "details": {
                "block_id": str(incomplete.block_id),
                "block_name": "BSCS-1B",
                "missing_subject_codes": ["CIS 1101", "CIS 1103"],
            },
        },
        {
            "code": "COHORT_WITHOUT_CURRICULUM",
            "message": "BSIT-1A has no curriculum for BSIT entry year 2026-2027.",
            "details": {
                "block_id": str(uncovered.block_id),
                "block_name": "BSIT-1A",
                "program_code": "BSIT",
                "entry_year": "2026-2027",
            },
        },
    ]


def test_a_term_without_blocks_has_no_curriculum_warnings(db_session: Session):
    term = add(
        db_session, Term(academic_year="2026-2027", term_number=1, is_active=True)
    )

    client = make_api_client(db_session)
    body = client.get(f"/api/v1/terms/{term.term_id}/readiness").json()

    assert body["warnings"] == []


def test_a_term_year_outside_the_curriculum_format_warns_instead_of_failing(
    db_session: Session,
):
    # The term schema accepts any short label, so a term saved before
    # curricula existed can carry one resolution cannot read.
    term = add(db_session, Term(academic_year="2026", term_number=1, is_active=True))
    add(db_session, CourseProgram(program_code="BSCS", program_name="BSCS"))
    first = make_block(db_session, term, "BSCS", "A")
    second = make_block(db_session, term, "BSCS", "B")

    client = make_api_client(db_session)
    response = client.get(f"/api/v1/terms/{term.term_id}/readiness")

    assert response.status_code == 200
    body = response.json()
    assert not {blocker["code"] for blocker in body["blockers"]} & CURRICULUM_CODES
    assert body["warnings"] == [
        {
            "code": "COHORT_ENTRY_YEAR_UNREADABLE",
            "message": (
                f"{block.name} has no curriculum because the term's academic "
                "year '2026' is not in YYYY-YYYY format."
            ),
            "details": {
                "block_id": str(block.block_id),
                "block_name": block.name,
                "academic_year": "2026",
            },
        }
        for block in (first, second)
    ]


@pytest.mark.parametrize("term_number", [3, 4])
def test_a_term_past_the_curriculum_range_warns_instead_of_requiring_nothing(
    db_session: Session, term_number: int
):
    # The Term API accepts any term number from 1, but a curriculum records
    # only terms 1-3. Term 3 is the last one a requirement can name.
    term = add(
        db_session,
        Term(academic_year="2026-2027", term_number=term_number, is_active=True),
    )
    add(db_session, CourseProgram(program_code="BSCS", program_name="BSCS"))
    add(
        db_session,
        Subject(
            subject_code="CIS 1101",
            subject_title="CIS 1101",
            units=3,
            subject_type="LEC",
        ),
    )
    curriculum = add(
        db_session, Curriculum(program_code="BSCS", effective_year="2026-2027")
    )
    add(
        db_session,
        CurriculumRequirement(
            curriculum_id=curriculum.curriculum_id,
            year_level=1,
            term_number=3,
            subject_code="CIS 1101",
        ),
    )
    block = make_block(db_session, term, "BSCS", "A")

    client = make_api_client(db_session)
    response = client.get(f"/api/v1/terms/{term.term_id}/readiness")

    assert response.status_code == 200
    body = response.json()
    assert not {blocker["code"] for blocker in body["blockers"]} & CURRICULUM_CODES
    if term_number == 3:
        assert [warning["code"] for warning in body["warnings"]] == [
            "COHORT_MISSING_REQUIRED_SUBJECT"
        ]
    else:
        assert body["warnings"] == [
            {
                "code": "COHORT_TERM_OUTSIDE_CURRICULUM",
                "message": (
                    "BSCS-1A has no curriculum requirements because term 4 is "
                    "outside the curriculum's terms 1-3."
                ),
                "details": {
                    "block_id": str(block.block_id),
                    "block_name": "BSCS-1A",
                    "term_number": 4,
                },
            }
        ]
