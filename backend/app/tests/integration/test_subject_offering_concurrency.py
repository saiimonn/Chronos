"""Group No. allocation under real concurrent PostgreSQL transactions.

These tests commit rather than rolling back inside one transaction: the whole
point is what two committed transactions do to each other, which a savepoint
inside a single connection cannot show. Every test cleans up the rows it wrote.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Subject, SubjectOffering, Term
from app.schemas.offering import OfferingCreate
from app.services.catalog import offerings as service

pytestmark = pytest.mark.postgres

SUBJECT_CODE = "CIS 9101"
OTHER_SUBJECT_CODE = "CIS 9102"


@pytest.fixture
def committed_term(migrated_engine):
    """A committed active term and two subjects, removed again afterwards.

    Concurrency needs rows that other connections can see, so this data is
    committed instead of living inside one test transaction.
    """
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        previously_active = session.scalars(
            select(Term).where(Term.is_active)
        ).all()
        for term in previously_active:
            term.is_active = False
        term = Term(
            term_id=uuid4(), academic_year="2099-2100", term_number=1, is_active=True
        )
        session.add(term)
        session.add(
            Subject(
                subject_code=SUBJECT_CODE,
                subject_title="Concurrency Practicum",
                units=3,
                subject_type="LEC",
            )
        )
        session.add(
            Subject(
                subject_code=OTHER_SUBJECT_CODE,
                subject_title="Concurrency Laboratory",
                units=1,
                subject_type="LAB",
            )
        )
        session.commit()
        term_id = term.term_id

    yield term_id

    with make_session() as session:
        session.execute(
            delete(SubjectOffering).where(SubjectOffering.term_id == term_id)
        )
        session.execute(
            delete(Subject).where(
                Subject.subject_code.in_([SUBJECT_CODE, OTHER_SUBJECT_CODE])
            )
        )
        session.execute(delete(Term).where(Term.term_id == term_id))
        for term in session.scalars(select(Term)).all():
            term.is_active = False
        session.commit()


def create_in_own_session(make_session, term_id, subject_code=SUBJECT_CODE) -> int:
    with make_session() as session:
        offering = service.create_offering(
            session, OfferingCreate(term_id=term_id, subject_code=subject_code)
        )
        return offering.group_no


def test_concurrent_creates_receive_distinct_consecutive_group_numbers(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)
    writers = 5

    with ThreadPoolExecutor(max_workers=writers) as pool:
        assigned = list(
            pool.map(
                lambda _: create_in_own_session(make_session, committed_term),
                range(writers),
            )
        )

    assert sorted(assigned) == [1, 2, 3, 4, 5]


def test_concurrent_creates_for_different_subjects_number_independently(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)
    subject_codes = [SUBJECT_CODE, OTHER_SUBJECT_CODE] * 3

    with ThreadPoolExecutor(max_workers=len(subject_codes)) as pool:
        assigned = list(
            pool.map(
                lambda code: (
                    code,
                    create_in_own_session(make_session, committed_term, code),
                ),
                subject_codes,
            )
        )

    for code in (SUBJECT_CODE, OTHER_SUBJECT_CODE):
        numbers = sorted(number for subject, number in assigned if subject == code)
        assert numbers == [1, 2, 3]


def test_duplicate_group_no_is_refused_by_the_database(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        session.add(
            SubjectOffering(
                term_id=committed_term, subject_code=SUBJECT_CODE, group_no=1
            )
        )
        session.commit()

        session.add(
            SubjectOffering(
                term_id=committed_term, subject_code=SUBJECT_CODE, group_no=1
            )
        )
        with pytest.raises(IntegrityError, match="uq_subject_offering"):
            session.commit()


def test_group_no_must_be_positive(migrated_engine, committed_term):
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as session:
        session.add(
            SubjectOffering(
                term_id=committed_term, subject_code=SUBJECT_CODE, group_no=0
            )
        )
        with pytest.raises(IntegrityError, match="ck_subject_offering_group_no_positive"):
            session.commit()


def test_group_no_reuse_after_the_highest_offering_is_deleted(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)
    first = create_in_own_session(make_session, committed_term)
    second = create_in_own_session(make_session, committed_term)
    assert (first, second) == (1, 2)

    with make_session() as session:
        highest = session.scalars(
            select(SubjectOffering).where(SubjectOffering.group_no == 2)
        ).one()
        service.delete_offering(session, highest.offering_id)

    assert create_in_own_session(make_session, committed_term) == 2


def test_deleting_a_middle_offering_leaves_the_others_untouched(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)
    for _ in range(3):
        create_in_own_session(make_session, committed_term)

    with make_session() as session:
        middle = session.scalars(
            select(SubjectOffering).where(SubjectOffering.group_no == 2)
        ).one()
        service.delete_offering(session, middle.offering_id)

    with make_session() as session:
        remaining = session.scalars(
            select(SubjectOffering.group_no)
            .where(SubjectOffering.term_id == committed_term)
            .order_by(SubjectOffering.group_no)
        ).all()
    assert list(remaining) == [1, 3]


def test_a_session_that_rolls_back_frees_the_number_it_held(
    migrated_engine, committed_term
):
    make_session = sessionmaker(bind=migrated_engine)

    with make_session() as abandoned:
        abandoned.add(
            SubjectOffering(
                term_id=committed_term, subject_code=SUBJECT_CODE, group_no=1
            )
        )
        abandoned.flush()
        abandoned.rollback()

    assert create_in_own_session(make_session, committed_term) == 1
