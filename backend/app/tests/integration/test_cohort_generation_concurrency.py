"""Cohort generation under real concurrent PostgreSQL transactions.

These tests commit rather than rolling back inside one transaction: the point
is what two committed transactions do to each other, which a savepoint inside
a single connection cannot show. Every test cleans up the rows it wrote.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

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
from app.services.catalog import cohort_generation

pytestmark = pytest.mark.postgres

PROGRAM_CODE = "BSCONC"
OTHER_PROGRAM_CODE = "BSCONC2"
SUBJECT_CODES = ("CIS 9201", "CIS 9201L")


@pytest.fixture
def committed_cohort(migrated_engine):
    """A committed active term, two programs, and their shared curriculum.

    Concurrency needs rows that other connections can see, so this data is
    committed instead of living inside one test transaction.
    """
    make_session = sessionmaker(bind=migrated_engine)
    programs = (PROGRAM_CODE, OTHER_PROGRAM_CODE)
    with make_session() as session:
        for term in session.scalars(select(Term).where(Term.is_active)).all():
            term.is_active = False
        term = Term(
            term_id=uuid4(), academic_year="2099-2100", term_number=1, is_active=True
        )
        session.add(term)
        for code, subject_type in zip(SUBJECT_CODES, ("LEC", "LAB"), strict=True):
            session.add(
                Subject(
                    subject_code=code,
                    subject_title=f"Concurrency {code}",
                    units=3,
                    subject_type=subject_type,
                )
            )
        for program_code in programs:
            session.add(
                CourseProgram(
                    program_code=program_code,
                    program_name=f"Concurrency {program_code}",
                )
            )
        session.flush()
        for program_code in programs:
            curriculum = Curriculum(
                program_code=program_code, effective_year="2099-2100"
            )
            session.add(curriculum)
            session.flush()
            for code in SUBJECT_CODES:
                session.add(
                    CurriculumRequirement(
                        curriculum_id=curriculum.curriculum_id,
                        year_level=1,
                        term_number=1,
                        subject_code=code,
                    )
                )
        session.commit()
        term_id = term.term_id

    yield term_id

    with make_session() as session:
        block_ids = select(BlockSection.block_id).where(
            BlockSection.term_id == term_id
        )
        session.execute(
            delete(BlockSectionOffering).where(
                BlockSectionOffering.block_id.in_(block_ids)
            )
        )
        session.execute(delete(BlockSection).where(BlockSection.term_id == term_id))
        session.execute(
            delete(SubjectOffering).where(SubjectOffering.term_id == term_id)
        )
        curriculum_ids = select(Curriculum.curriculum_id).where(
            Curriculum.program_code.in_(programs)
        )
        session.execute(
            delete(CurriculumRequirement).where(
                CurriculumRequirement.curriculum_id.in_(curriculum_ids)
            )
        )
        session.execute(delete(Curriculum).where(Curriculum.program_code.in_(programs)))
        session.execute(
            delete(CourseProgram).where(CourseProgram.program_code.in_(programs))
        )
        session.execute(delete(Subject).where(Subject.subject_code.in_(SUBJECT_CODES)))
        session.execute(delete(Term).where(Term.term_id == term_id))
        session.commit()


@pytest.fixture
def slow_planning(monkeypatch: pytest.MonkeyPatch):
    """Hold every request between reading the cohort and writing to it.

    Without serialization, concurrent requests would all read an empty cohort
    inside this window and then collide on the unique constraints.
    """
    plan = cohort_generation.plan_cohort_classes

    def plan_slowly(*args, **kwargs):
        time.sleep(0.3)
        return plan(*args, **kwargs)

    monkeypatch.setattr(cohort_generation, "plan_cohort_classes", plan_slowly)


def generate_in_own_session(make_session, term_id, program_code=PROGRAM_CODE):
    with make_session() as session:
        report = cohort_generation.generate_cohort_classes(
            session, term_id, program_code, 1, ["A", "B"]
        )
        return (
            sum(block.created for block in report.blocks),
            sum(item.created for block in report.blocks for item in block.classes),
        )


def test_duplicate_concurrent_requests_create_the_cohort_once(
    migrated_engine, committed_cohort, slow_planning
):
    make_session = sessionmaker(bind=migrated_engine)
    writers = 4

    with ThreadPoolExecutor(max_workers=writers) as pool:
        results = list(
            pool.map(
                lambda _: generate_in_own_session(make_session, committed_cohort),
                range(writers),
            )
        )

    # Exactly one request did the work; the rest found it already done.
    assert sorted(results) == [(0, 0)] * (writers - 1) + [(2, 4)]
    with make_session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(BlockSection)
            .where(BlockSection.term_id == committed_cohort)
        ) == 2
        group_numbers = session.scalars(
            select(SubjectOffering.group_no)
            .where(SubjectOffering.term_id == committed_cohort)
            .order_by(SubjectOffering.subject_code, SubjectOffering.group_no)
        ).all()
    # Paired A and B share the LEC offering; each takes its own LAB.
    assert list(group_numbers) == [1, 1, 2]


def test_cohorts_sharing_subjects_generate_concurrently(
    migrated_engine, committed_cohort, slow_planning
):
    make_session = sessionmaker(bind=migrated_engine)
    programs = [PROGRAM_CODE, OTHER_PROGRAM_CODE] * 2

    with ThreadPoolExecutor(max_workers=len(programs)) as pool:
        results = list(
            pool.map(
                lambda code: generate_in_own_session(
                    make_session, committed_cohort, code
                ),
                programs,
            )
        )

    # Each cohort was created once, and their offerings share one numbering.
    assert sorted(results) == [(0, 0), (0, 0), (2, 4), (2, 4)]
    with make_session() as session:
        group_numbers = session.scalars(
            select(SubjectOffering.group_no)
            .where(SubjectOffering.term_id == committed_cohort)
            .order_by(SubjectOffering.subject_code, SubjectOffering.group_no)
        ).all()
    # One shared LEC per cohort pair, one LAB per Block.
    assert list(group_numbers) == [1, 2, 1, 2, 3, 4]
