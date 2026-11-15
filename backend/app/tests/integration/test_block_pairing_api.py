"""Blocks pair by letter (A-B, C-D, ...): their LECs merge, their LABs link."""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.tests.integration.test_blocks_api import (
    LAB_SUBJECT,
    block_payload,
    make_offering,
    prepare,
)
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def make_block(client, term_id: str, letter: str, **overrides) -> str:
    created = client.post(
        "/api/v1/block-sections",
        json=block_payload(term_id, section_letter=letter, **overrides),
    )
    assert created.status_code == 201, created.text
    return created.json()["block_id"]


def take(client, term_id: str, block_id: str, subject_code: str) -> dict:
    added = client.post(
        f"/api/v1/block-sections/{block_id}/classes",
        json={"offering_id": make_offering(client, term_id, subject_code)},
    )
    assert added.status_code == 201, added.text
    return added.json()


def classes_of(client, block_id: str) -> dict[str, dict]:
    listed = client.get(f"/api/v1/block-sections/{block_id}/classes").json()["items"]
    return {item["subject_code"]: item for item in listed}


@pytest.mark.parametrize("first_letter", ["A", "B"])
def test_paired_blocks_share_the_lecture_whichever_takes_it_first(
    db_session: Session, first_letter
):
    client = make_api_client(db_session)
    term_id = prepare(client)
    blocks = {letter: make_block(client, term_id, letter) for letter in "AB"}
    second_letter = "B" if first_letter == "A" else "A"

    first = take(client, term_id, blocks[first_letter], "CS101")
    abandoned = make_offering(client, term_id, "CS101")
    second = client.post(
        f"/api/v1/block-sections/{blocks[second_letter]}/classes",
        json={"offering_id": abandoned},
    ).json()

    a_lecture = classes_of(client, blocks["A"])["CS101"]
    b_lecture = classes_of(client, blocks["B"])["CS101"]
    assert a_lecture["offering_id"] == b_lecture["offering_id"]
    # The response names the class the Block actually ended up with.
    assert second["offering_id"] == a_lecture["offering_id"]
    # B always moves onto A's offering, and the one it left is removed.
    survivor = first["offering_id"] if first_letter == "A" else abandoned
    assert a_lecture["offering_id"] == survivor
    gone = abandoned if first_letter == "A" else first["offering_id"]
    assert client.get(f"/api/v1/subject-offerings/{gone}").status_code == 404


def test_paired_labs_stay_separate_and_are_linked_for_the_ga(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    block_a = make_block(client, term_id, "A")
    block_b = make_block(client, term_id, "B")
    lab_a = take(client, term_id, block_a, LAB_SUBJECT["subject_code"])
    lab_b = take(client, term_id, block_b, LAB_SUBJECT["subject_code"])

    assert lab_a["offering_id"] != lab_b["offering_id"]
    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(UUID(term_id))
    a, b = UUID(lab_a["class_id"]), UUID(lab_b["class_id"])
    assert problem.lab_partners == {a: b, b: a}


def test_blocks_outside_a_letter_pair_keep_their_own_lectures(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    block_a = make_block(client, term_id, "A")
    block_c = make_block(client, term_id, "C")
    block_2b = make_block(client, term_id, "B", year_level=2)
    offerings = {
        take(client, term_id, block_id, "CS101")["offering_id"]
        for block_id in (block_a, block_c, block_2b)
    }
    assert len(offerings) == 3
