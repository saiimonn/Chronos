"""Inactive catalog records stay resolvable but are never new scheduling input.

Subjects, rooms, buildings and course programs are retired with a flag rather
than deleted, so history keeps naming them. These cases pin where that flag is
honoured: offering and Block creation, the scheduling problem, and readiness.
"""

import pytest
from sqlalchemy.orm import Session

from app.models import Room
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.services.readiness import analyze_readiness
from app.tests.integration.test_blocks_api import block_payload, prepare
from app.tests.integration.test_schedule import make_problem_data
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def test_an_inactive_subject_cannot_be_offered(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    assert client.delete("/api/v1/subjects/CS101").status_code == 204

    refused = client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": "CS101"},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "INACTIVE_REFERENCE"
    assert refused.json()["details"] == {"field": "subject_code", "value": "CS101"}

    client.patch("/api/v1/subjects/CS101", json={"is_active": True})
    assert client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": "CS101"},
    ).status_code == 201


def test_an_inactive_program_takes_no_new_blocks(db_session: Session):
    client = make_api_client(db_session)
    term_id = prepare(client)
    assert client.delete("/api/v1/course-programs/BSCS").status_code == 204

    refused = client.post("/api/v1/block-sections", json=block_payload(term_id))

    assert refused.status_code == 409
    assert refused.json()["code"] == "INACTIVE_REFERENCE"


def test_retired_records_stay_in_the_problem_but_block_readiness(db_session: Session):
    data = make_problem_data(db_session)
    term, subject, program = data[0], data[1], data[2]
    db_session.add(
        Room(room_code="RET-1", building_name="b", capacity=40, room_type="lecture")
    )
    db_session.flush()
    subject.is_active = False
    program.is_active = False
    db_session.get(Room, "RET-1").is_active = False
    db_session.flush()

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(
        term.term_id
    )

    # Still resolvable, so schedules naming them validate and export...
    assert [(r.room_code, r.is_active) for r in problem.rooms] == [("RET-1", False)]
    assert [s.is_active for s in problem.subjects] == [False]
    assert [p.is_active for p in problem.programs] == [False]
    # ...but they neither count as rooms nor let the term be scheduled.
    blockers = {b.code for b in analyze_readiness(problem).blockers}
    assert {
        "NO_ROOMS",
        "INACTIVE_SUBJECT_OFFERED",
        "INACTIVE_PROGRAM_BLOCKS",
    } <= blockers
