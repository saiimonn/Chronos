"""Schema-level guarantees for CHR-BLK-002 Blocks and their offering links.

These assertions belong to the database rather than to a service: a Block that
duplicates a cohort, stores a lowercase letter its derived name would upper, or
links to an offering from another term must be rejected no matter which code
path wrote it. The LAB/LEC block-count rule is deliberately absent here — it
depends on the subject type two joins away and lives in the service layer.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Subject,
    SubjectOffering,
    Term,
)

pytestmark = pytest.mark.postgres


def make_term(session: Session, **overrides) -> Term:
    values = {"academic_year": "2025-2026", "term_number": 1, "is_active": True}
    term = Term(**(values | overrides))
    session.add(term)
    session.flush()
    return term


def make_subject(session: Session, **overrides) -> Subject:
    values = {
        "subject_code": "CS101",
        "subject_title": "Introduction to Computing",
        "units": 3,
        "subject_type": "LEC",
    }
    subject = Subject(**(values | overrides))
    session.add(subject)
    session.flush()
    return subject


def make_program(session: Session) -> CourseProgram:
    program = CourseProgram(program_code="BSCS", program_name="BS Computer Science")
    session.add(program)
    session.flush()
    return program


def make_offering(
    session: Session, term: Term, subject: Subject, group_no: int = 1
) -> SubjectOffering:
    offering = SubjectOffering(
        term_id=term.term_id, subject_code=subject.subject_code, group_no=group_no
    )
    session.add(offering)
    session.flush()
    return offering


def make_block(session: Session, term: Term, program: CourseProgram, **overrides):
    values = {
        "term_id": term.term_id,
        "program_code": program.program_code,
        "year_level": 1,
        "section_letter": "A",
    }
    block = BlockSection(**(values | overrides))
    session.add(block)
    session.flush()
    return block


def link(block: BlockSection, offering: SubjectOffering) -> BlockSectionOffering:
    return BlockSectionOffering(
        block_id=block.block_id,
        offering_id=offering.offering_id,
        term_id=offering.term_id,
        subject_code=offering.subject_code,
    )


def test_a_link_is_a_class_with_an_identifier_of_its_own(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)
    subject = make_subject(db_session)
    offering = make_offering(db_session, term, subject)
    block = make_block(db_session, term, program)

    row = link(block, offering)
    db_session.add(row)
    db_session.flush()

    assert row.class_id is not None


def test_block_persists_without_a_subject(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)

    block = make_block(db_session, term, program)

    assert block.block_id is not None
    assert block.name == "BSCS-1A"
    assert block.capacity == 20


def test_block_rejects_a_duplicate_cohort_in_one_term(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)
    make_block(db_session, term, program)

    db_session.add(
        BlockSection(
            term_id=term.term_id,
            program_code=program.program_code,
            year_level=1,
            section_letter="A",
        )
    )

    with pytest.raises(IntegrityError, match="uq_block_section_cohort"):
        db_session.flush()


def test_block_rejects_a_lowercase_section_letter(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)

    db_session.add(
        BlockSection(
            term_id=term.term_id,
            program_code=program.program_code,
            year_level=1,
            section_letter="a",
        )
    )

    with pytest.raises(IntegrityError, match="ck_block_section_letter_uppercase"):
        db_session.flush()


def test_block_rejects_a_year_level_outside_one_to_five(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)

    db_session.add(
        BlockSection(
            term_id=term.term_id,
            program_code=program.program_code,
            year_level=6,
            section_letter="A",
        )
    )

    with pytest.raises(IntegrityError, match="ck_block_section_year_level_range"):
        db_session.flush()


def test_block_links_to_a_subject_offering(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)
    subject = make_subject(db_session)
    offering = make_offering(db_session, term, subject)
    block = make_block(db_session, term, program)

    db_session.add(link(block, offering))
    db_session.flush()
    db_session.refresh(block)

    assert [row.offering_id for row in block.offering_links] == [offering.offering_id]


def test_block_rejects_two_offerings_of_the_same_subject(db_session: Session):
    term = make_term(db_session)
    program = make_program(db_session)
    subject = make_subject(db_session)
    first = make_offering(db_session, term, subject, group_no=1)
    second = make_offering(db_session, term, subject, group_no=2)
    block = make_block(db_session, term, program)
    db_session.add(link(block, first))
    db_session.flush()

    db_session.add(link(block, second))

    with pytest.raises(IntegrityError, match="uq_block_section_offering_subject"):
        db_session.flush()


def test_block_rejects_an_offering_from_another_term(db_session: Session):
    block_term = make_term(db_session)
    offering_term = make_term(db_session, academic_year="2026-2027", is_active=False)
    program = make_program(db_session)
    subject = make_subject(db_session)
    offering = make_offering(db_session, offering_term, subject)
    block = make_block(db_session, block_term, program)

    db_session.add(
        BlockSectionOffering(
            block_id=block.block_id,
            offering_id=offering.offering_id,
            term_id=block.term_id,
            subject_code=subject.subject_code,
        )
    )

    with pytest.raises(IntegrityError, match="fk_block_section_offering_offering"):
        db_session.flush()


def test_link_rejects_a_subject_code_that_is_not_the_offering_subject(
    db_session: Session,
):
    term = make_term(db_session)
    program = make_program(db_session)
    subject = make_subject(db_session)
    make_subject(db_session, subject_code="CS102", subject_title="Data Structures")
    offering = make_offering(db_session, term, subject)
    block = make_block(db_session, term, program)

    db_session.add(
        BlockSectionOffering(
            block_id=block.block_id,
            offering_id=offering.offering_id,
            term_id=term.term_id,
            subject_code="CS102",
        )
    )

    with pytest.raises(IntegrityError, match="fk_block_section_offering_offering"):
        db_session.flush()


def test_link_rejects_an_unknown_block(db_session: Session):
    term = make_term(db_session)
    subject = make_subject(db_session)
    offering = make_offering(db_session, term, subject)

    db_session.add(
        BlockSectionOffering(
            block_id=uuid.uuid4(),
            offering_id=offering.offering_id,
            term_id=term.term_id,
            subject_code=subject.subject_code,
        )
    )

    with pytest.raises(IntegrityError, match="fk_block_section_offering_block"):
        db_session.flush()
