"""The CHR-API-002 done-when criterion, exercised end to end.

Starting from an empty database, this drives only HTTP endpoints to prepare a
term, then checks that the same term assembles into a complete
SchedulingProblem through the repository the GA will read.
"""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres


def test_a_complete_scheduling_problem_can_be_prepared_through_the_api(
    db_session: Session,
):
    client = make_api_client(db_session)

    term_id = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()["term_id"]
    assert client.post(f"/api/v1/terms/{term_id}/select").json()["is_active"] is True

    assert (
        client.post(
            "/api/v1/course-programs",
            json={"program_code": "BSCS", "program_name": "BS Computer Science"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/subjects",
            json={
                "subject_code": "CS101",
                "subject_title": "Introduction to Computing",
                "units": 3,
                "subject_type": "LEC",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/buildings",
            json={"building_name": "Lawton", "building_code": "LT"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/rooms",
            json={
                "room_code": "LC101",
                "building_name": "Lawton",
                "room_type": "lecture",
            },
        ).status_code
        == 201
    )

    # The term was created with its canonical grid, so the slots are read.
    slots = client.get("/api/v1/time-slots", params={"term_id": term_id}).json()["items"]
    slot_ids = [item["time_slot_id"] for item in slots]

    faculty_id = client.post(
        "/api/v1/faculty", json={"full_name": "Ada Lovelace", "is_full_time": False}
    ).json()["faculty_id"]

    block_id = client.post(
        "/api/v1/block-sections",
        json={
            "term_id": term_id,
            "program_code": "BSCS",
            "year_level": 1,
            "section_letter": "A",
        },
    ).json()["block_id"]
    offering_id = client.post(
        "/api/v1/subject-offerings",
        json={"term_id": term_id, "subject_code": "CS101"},
    ).json()["offering_id"]
    class_id = client.post(
        f"/api/v1/block-sections/{block_id}/classes",
        json={"offering_id": offering_id},
    ).json()["class_id"]

    assert (
        client.put(
            f"/api/v1/faculty/{faculty_id}/availability",
            json={"term_id": term_id, "time_slot_ids": slot_ids},
        ).status_code
        == 200
    )

    readiness = client.get(f"/api/v1/terms/{term_id}/readiness").json()
    assert readiness["blockers"] == []
    assert readiness["ready"] is True
    # No curriculum is recorded for BSCS, which is worth a warning but does not
    # stop generation.
    assert [warning["code"] for warning in readiness["warnings"]] == [
        "COHORT_WITHOUT_CURRICULUM"
    ]

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(UUID(term_id))

    assert str(problem.term.term_id) == term_id
    assert problem.term.is_active is True
    assert [str(item.faculty_id) for item in problem.faculty] == [faculty_id]
    assert problem.faculty[0].availability_mask == "1" * 48
    assert [item.subject_code for item in problem.subjects] == ["CS101"]
    assert problem.subjects[0].subject_type == "LEC"
    assert problem.subjects[0].duration_minutes == 90
    assert [item.room_code for item in problem.rooms] == ["LC101"]
    assert [item.program_code for item in problem.programs] == ["BSCS"]
    assert [str(item.class_id) for item in problem.classes] == [class_id]
    assert [str(item.time_slot_id) for item in problem.time_slots] == slot_ids
