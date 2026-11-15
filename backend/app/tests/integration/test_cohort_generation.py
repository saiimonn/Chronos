"""Generating a cohort's Blocks, offerings, and classes from its curriculum.

Driven through the API so the route, the executor, and the database constraints
are exercised together. Curricula have no API yet, so they are written through
the session the client shares.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    BlockSection,
    BlockSectionOffering,
    Curriculum,
    CurriculumRequirement,
    SubjectOffering,
)
from app.services.catalog import cohort_generation
from app.services.catalog.blocks import link_class
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

LEC = "CIS 2101"
LAB = "CIS 2101L"
PROGRAM = {"program_code": "BSCS", "program_name": "BS Computer Science"}


def prepare(client, db: Session, *, term_number: int = 1) -> str:
    """An active 2025-2026 term whose second years follow a 2023 curriculum.

    Second years in 2025-2026 entered in 2024-2025; with no 2024 row, the
    2023-2024 prospectus is the one in force.
    """
    term = client.post(
        "/api/v1/terms",
        json={"academic_year": "2025-2026", "term_number": term_number},
    ).json()
    client.post(f"/api/v1/terms/{term['term_id']}/select")
    client.post("/api/v1/course-programs", json=PROGRAM)
    for code, subject_type in ((LEC, "LEC"), (LAB, "LAB")):
        client.post(
            "/api/v1/subjects",
            json={
                "subject_code": code,
                "subject_title": f"Subject {code}",
                "units": 3,
                "subject_type": subject_type,
            },
        )
    curriculum = Curriculum(program_code="BSCS", effective_year="2023-2024")
    db.add(curriculum)
    db.flush()
    for code in (LEC, LAB):
        db.add(
            CurriculumRequirement(
                curriculum_id=curriculum.curriculum_id,
                year_level=2,
                term_number=1,
                subject_code=code,
            )
        )
    db.flush()
    return term["term_id"]


def generate(client, term_id: str, letters=("A", "B"), **overrides):
    return client.post(
        f"/api/v1/terms/{term_id}/cohorts/generate",
        json={
            "program_code": "BSCS",
            "year_level": 2,
            "section_letters": list(letters),
            **overrides,
        },
    )


def count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_generation_creates_blocks_offerings_and_classes(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    response = generate(client, term_id)

    assert response.status_code == 200
    body = response.json()
    assert body["curriculum_label"] == "BSCS 2023-2024"
    assert body["required_subject_codes"] == [LEC, LAB]
    assert [block["name"] for block in body["blocks"]] == ["BSCS-2A", "BSCS-2B"]
    assert all(block["created"] for block in body["blocks"])
    assert body["blocks_created"] == 2
    # A and B are paired: one shared LEC offering, a LAB offering each.
    assert body["offerings_created"] == 3
    assert body["classes_created"] == 4
    assert {
        (block["name"], item["group_label"])
        for block in body["blocks"]
        for item in block["classes"]
    } == {
        ("BSCS-2A", f"{LEC} G1"),
        ("BSCS-2A", f"{LAB} G1"),
        ("BSCS-2B", f"{LEC} G1"),
        ("BSCS-2B", f"{LAB} G2"),
    }
    assert count(db_session, BlockSectionOffering) == 4


def test_generation_is_idempotent(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    first = generate(client, term_id).json()

    second = generate(client, term_id)

    assert second.status_code == 200
    body = second.json()
    assert body["blocks_created"] == 0
    assert body["offerings_created"] == 0
    assert body["classes_created"] == 0
    assert not any(block["created"] for block in body["blocks"])
    assert all(
        not item["created"] for block in body["blocks"] for item in block["classes"]
    )
    # The report names the same rows the first run made.
    assert {
        item["class_id"] for block in body["blocks"] for item in block["classes"]
    } == {
        item["class_id"] for block in first["blocks"] for item in block["classes"]
    }
    assert count(db_session, BlockSection) == 2
    assert count(db_session, SubjectOffering) == 3


def test_adding_a_section_creates_only_that_block(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    generate(client, term_id, letters=("A", "B"))

    body = generate(client, term_id, letters=("A", "B", "C")).json()

    assert [block["created"] for block in body["blocks"]] == [False, False, True]
    assert body["classes_created"] == 2
    # C has no D yet, so its LEC is a new offering of its own.
    assert {
        item["group_label"] for item in body["blocks"][2]["classes"]
    } == {f"{LEC} G2", f"{LAB} G3"}


def test_a_later_partner_joins_the_existing_lecture(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    generate(client, term_id, letters=("A",))

    body = generate(client, term_id, letters=("A", "B")).json()

    a_lec, b_lec = (
        next(item for item in block["classes"] if item["subject_code"] == LEC)
        for block in body["blocks"]
    )
    assert b_lec["created"] is True
    assert b_lec["offering_id"] == a_lec["offering_id"]
    assert body["offerings_created"] == 1


def test_a_subject_already_taken_through_any_group_is_left_alone(
    db_session: Session,
):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    block_id = client.post(
        "/api/v1/block-sections",
        json={
            "term_id": term_id,
            "program_code": "BSCS",
            "year_level": 2,
            "section_letter": "A",
        },
    ).json()["block_id"]
    client.post(
        "/api/v1/subject-offerings", json={"term_id": term_id, "subject_code": LEC}
    )
    manual = client.post(
        "/api/v1/subject-offerings", json={"term_id": term_id, "subject_code": LEC}
    ).json()
    client.post(
        f"/api/v1/block-sections/{block_id}/classes",
        json={"offering_id": manual["offering_id"]},
    )

    body = generate(client, term_id, letters=("A",)).json()

    block = body["blocks"][0]
    assert block["created"] is False
    lec = next(item for item in block["classes"] if item["subject_code"] == LEC)
    assert lec["created"] is False
    assert lec["offering_id"] == manual["offering_id"]
    assert body["classes_created"] == 1


def test_new_offerings_continue_existing_group_numbers(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    for _ in range(3):
        client.post(
            "/api/v1/subject-offerings",
            json={"term_id": term_id, "subject_code": LEC},
        )

    body = generate(client, term_id, letters=("A",)).json()

    lec = next(
        item for item in body["blocks"][0]["classes"] if item["subject_code"] == LEC
    )
    assert lec["group_no"] == 4


def test_only_paired_sections_share_and_only_their_lectures(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    generate(client, term_id, letters=("A", "B", "C"))

    shared = db_session.execute(
        select(SubjectOffering.subject_code, func.count())
        .join(
            BlockSectionOffering,
            BlockSectionOffering.offering_id == SubjectOffering.offering_id,
        )
        .group_by(SubjectOffering.offering_id, SubjectOffering.subject_code)
        .having(func.count() > 1)
    ).all()
    assert shared == [(LEC, 2)]
    assert count(db_session, SubjectOffering) == 5


def test_generation_merges_split_lectures_onto_consecutive_groups(
    db_session: Session,
):
    """Blocks linked before lecture pairing: A-D each on its own LEC group.

    Re-generating puts A+B on G1 and C+D on G2, with no gap left behind.
    """
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    blocks = {
        letter: db_session.get(
            BlockSection,
            client.post(
                "/api/v1/block-sections",
                json={
                    "term_id": term_id,
                    "program_code": "BSCS",
                    "year_level": 2,
                    "section_letter": letter,
                },
            ).json()["block_id"],
        )
        for letter in "ABCD"
    }
    # Linked straight through the service, skipping the merge an API edit runs.
    groups = {}
    for letter, block in blocks.items():
        groups[letter] = client.post(
            "/api/v1/subject-offerings",
            json={"term_id": term_id, "subject_code": LEC},
        ).json()["offering_id"]
        link_class(db_session, block, db_session.get(SubjectOffering, groups[letter]))
    db_session.flush()

    body = generate(client, term_id, letters=("A", "B", "C", "D")).json()

    lec = {
        block["name"][-1]: next(
            item for item in block["classes"] if item["subject_code"] == LEC
        )
        for block in body["blocks"]
    }
    assert lec["A"]["offering_id"] == lec["B"]["offering_id"] == groups["A"]
    assert lec["C"]["offering_id"] == lec["D"]["offering_id"] == groups["C"]
    assert lec["A"]["group_label"] == lec["B"]["group_label"] == f"{LEC} G1"
    assert lec["C"]["group_label"] == lec["D"]["group_label"] == f"{LEC} G2"
    assert db_session.scalars(
        select(SubjectOffering.group_no)
        .where(SubjectOffering.subject_code == LEC)
        .order_by(SubjectOffering.group_no)
    ).all() == [1, 2]
    labs = {
        next(item for item in block["classes"] if item["subject_code"] == LAB)[
            "offering_id"
        ]
        for block in body["blocks"]
    }
    assert len(labs) == 4


def test_empty_requirements_create_blocks_with_a_note(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session, term_number=3)

    response = generate(client, term_id, letters=("A",))

    assert response.status_code == 200
    body = response.json()
    assert body["required_subject_codes"] == []
    assert body["blocks_created"] == 1
    assert body["blocks"][0]["classes"] == []
    assert "requires no subjects" in body["blocks"][0]["note"]


def test_no_curriculum_is_refused_and_nothing_is_written(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    client.post(
        "/api/v1/course-programs",
        json={"program_code": "BSIT", "program_name": "BS Information Technology"},
    )

    response = generate(client, term_id, program_code="BSIT")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "NO_APPLICABLE_CURRICULUM"
    assert body["details"] == {
        "program_code": "BSIT",
        "entry_year": "2024-2025",
    }
    assert count(db_session, BlockSection) == 0
    assert count(db_session, SubjectOffering) == 0


def test_generation_is_refused_on_an_inactive_term(db_session: Session):
    client = make_api_client(db_session)
    prepare(client, db_session)
    inactive = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 2}
    ).json()

    response = generate(client, inactive["term_id"])

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_NOT_ACTIVE"
    assert count(db_session, BlockSection) == 0


def test_an_unknown_program_is_a_bad_reference(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    response = generate(client, term_id, program_code="NOPE")

    assert response.status_code == 409
    assert response.json()["code"] == "UNKNOWN_REFERENCE"


def test_a_failure_part_way_leaves_nothing_written(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    # Fail the third class link, after two Blocks and two offerings are already
    # flushed, and check none of them survive.
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)
    calls = []

    def failing_link_class(db, block, offering):
        calls.append(offering.subject_code)
        if len(calls) == 3:
            raise APIError(409, "FORCED_FAILURE", "Forced by the test.")
        return link_class(db, block, offering)

    monkeypatch.setattr(cohort_generation, "link_class", failing_link_class)

    response = generate(client, term_id)

    assert response.status_code == 409
    assert response.json()["code"] == "FORCED_FAILURE"
    assert count(db_session, BlockSection) == 0
    assert count(db_session, SubjectOffering) == 0
    assert count(db_session, BlockSectionOffering) == 0


def test_repeated_letters_ask_for_one_block(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    body = generate(client, term_id, letters=("a", "A")).json()

    assert [block["name"] for block in body["blocks"]] == ["BSCS-2A"]
    assert body["blocks_created"] == 1


def test_manual_block_and_class_creation_is_unchanged(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    created = client.post(
        "/api/v1/block-sections",
        json={
            "term_id": term_id,
            "program_code": "BSCS",
            "year_level": 2,
            "section_letter": "d",
        },
    )
    assert created.status_code == 201
    assert created.json()["name"] == "BSCS-2D"
    offering = client.post(
        "/api/v1/subject-offerings", json={"term_id": term_id, "subject_code": LAB}
    ).json()
    linked = client.post(
        f"/api/v1/block-sections/{created.json()['block_id']}/classes",
        json={"offering_id": offering["offering_id"]},
    )
    assert linked.status_code == 201
    other = client.post(
        "/api/v1/block-sections",
        json={
            "term_id": term_id,
            "program_code": "BSCS",
            "year_level": 2,
            "section_letter": "E",
        },
    ).json()
    over_limit = client.post(
        f"/api/v1/block-sections/{other['block_id']}/classes",
        json={"offering_id": offering["offering_id"]},
    )
    assert over_limit.status_code == 409
    assert over_limit.json()["code"] == "OFFERING_BLOCK_LIMIT_REACHED"


def test_a_scheduler_may_generate(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client, db_session)

    assert generate(client, term_id).status_code == 200
