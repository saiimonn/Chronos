"""Closing the gaps a lecture merge leaves in a subject's Group Nos."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SubjectOffering
from app.services.catalog.offerings import compact_group_numbers
from app.tests.integration.test_block_pairing_api import classes_of, make_block, take
from app.tests.integration.test_blocks_api import prepare
from app.tests.integration.test_schedule import make_assignment, make_problem_data
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def group_numbers(db: Session, term_id, subject_code: str) -> list[int]:
    return db.scalars(
        select(SubjectOffering.group_no)
        .where(
            SubjectOffering.term_id == term_id,
            SubjectOffering.subject_code == subject_code,
        )
        .order_by(SubjectOffering.group_no)
    ).all()


def add_group(db: Session, term_id, subject_code: str, group_no: int) -> SubjectOffering:
    offering = SubjectOffering(
        term_id=term_id, subject_code=subject_code, group_no=group_no
    )
    db.add(offering)
    db.flush()
    return offering


def test_groups_close_up_in_their_existing_order(db_session: Session):
    term, subject, *_ = make_problem_data(db_session)
    third = add_group(db_session, term.term_id, subject.subject_code, 3)
    fifth = add_group(db_session, term.term_id, subject.subject_code, 5)

    assert compact_group_numbers(db_session, term.term_id, subject.subject_code)

    assert group_numbers(db_session, term.term_id, subject.subject_code) == [1, 2, 3]
    assert (third.group_no, fifth.group_no) == (2, 3)


def test_a_scheduled_subject_keeps_its_numbers(db_session: Session):
    data = make_problem_data(db_session)
    term, subject = data[0], data[1]
    db_session.add(make_assignment(data))
    add_group(db_session, term.term_id, subject.subject_code, 3)

    assert not compact_group_numbers(db_session, term.term_id, subject.subject_code)

    assert group_numbers(db_session, term.term_id, subject.subject_code) == [1, 3]


def test_a_merge_through_the_api_leaves_no_gap(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    blocks = {letter: make_block(client, term_id, letter) for letter in "ABC"}
    # B takes G1, C takes G2, then A takes G3: B moves onto A's G3 and G1 is
    # removed, which would leave G2, G3 without compaction.
    for letter in "BCA":
        take(client, term_id, blocks[letter], "CS101")

    offerings = {
        letter: classes_of(client, block_id)["CS101"]["offering_id"]
        for letter, block_id in blocks.items()
    }
    numbers = {
        letter: db_session.get(SubjectOffering, offering_id).group_no
        for letter, offering_id in offerings.items()
    }
    assert offerings["A"] == offerings["B"]
    assert numbers == {"A": 2, "B": 2, "C": 1}
    assert group_numbers(db_session, term_id, "CS101") == [1, 2]
