import pytest
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    Curriculum,
    CurriculumRequirement,
    SubjectOffering,
)
from app.tests.support.api import make_api_client, make_client

pytestmark = pytest.mark.postgres

SUBJECT = {
    "subject_code": "CS101",
    "subject_title": "Introduction to Computing",
    "units": 3,
    "subject_type": "LEC",
}
SUBJECT_RESPONSE = {**SUBJECT, "duration_minutes": 90, "is_active": True, "preferred_day_patterns": [], "allowed_room_codes": [], "placements": []}
ROOM = {
    "room_code": "LB204",
    "building_name": "Lawton",
    "room_type": "laboratory",
}
ROOM_RESPONSE = {**ROOM, "capacity": 20, "expanded": False, "is_active": True, "allowed_subject_codes": []}
BUILDING = {"building_name": "Lawton", "building_code": "LT"}
PROGRAM = {"program_code": "BSCS", "program_name": "BS Computer Science"}
PROGRAM_RESPONSE = {**PROGRAM, "is_active": True}


def test_subject_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)

    created = client.post("/api/v1/subjects", json=SUBJECT)
    assert created.status_code == 201
    assert created.json() == SUBJECT_RESPONSE

    assert client.get("/api/v1/subjects").json()["items"] == [SUBJECT_RESPONSE]
    assert client.get("/api/v1/subjects/CS101").json() == SUBJECT_RESPONSE

    patched = client.patch("/api/v1/subjects/CS101", json={"units": 4})
    assert patched.status_code == 200
    assert patched.json()["units"] == 4

    # DELETE retires rather than removes: the record stays resolvable by code
    # but drops out of the default (scheduling) list.
    assert client.delete("/api/v1/subjects/CS101").status_code == 204
    retired = client.get("/api/v1/subjects/CS101")
    assert retired.status_code == 200
    assert retired.json()["is_active"] is False
    assert client.get("/api/v1/subjects").json()["items"] == []
    listed = client.get("/api/v1/subjects", params={"include_inactive": True})
    assert [item["subject_code"] for item in listed.json()["items"]] == ["CS101"]

    # Deactivating again is a no-op, and PATCH restores it.
    assert client.delete("/api/v1/subjects/CS101").status_code == 204
    restored = client.patch("/api/v1/subjects/CS101", json={"is_active": True})
    assert restored.json()["is_active"] is True
    assert len(client.get("/api/v1/subjects").json()["items"]) == 1


def test_subject_lists_its_prospectus_placements(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/course-programs", json=PROGRAM)
    client.post("/api/v1/course-programs", json={"program_code": "BSIT", "program_name": "BS IT"})
    client.post("/api/v1/subjects", json=SUBJECT)
    # The same slot in two curriculum versions is reported once.
    for program, year, level, term in (
        ("BSCS", "2023-2024", 2, 1),
        ("BSCS", "2024-2025", 2, 1),
        ("BSIT", "2023-2024", 1, 3),
    ):
        curriculum = db_session.query(Curriculum).filter_by(
            program_code=program, effective_year=year
        ).one_or_none() or Curriculum(program_code=program, effective_year=year)
        db_session.add(curriculum)
        db_session.flush()
        db_session.add(
            CurriculumRequirement(
                curriculum_id=curriculum.curriculum_id,
                year_level=level,
                term_number=term,
                subject_code="CS101",
            )
        )
    db_session.flush()

    expected = [
        {"program_code": "BSCS", "year_level": 2, "term_number": 1},
        {"program_code": "BSIT", "year_level": 1, "term_number": 3},
    ]
    assert client.get("/api/v1/subjects").json()["items"][0]["placements"] == expected
    assert client.get("/api/v1/subjects/CS101").json()["placements"] == expected


def test_room_and_program_crud_round_trip(db_session: Session):
    client = make_api_client(db_session)

    client.post("/api/v1/buildings", json=BUILDING)
    created_room = client.post("/api/v1/rooms", json=ROOM)
    assert created_room.status_code == 201
    assert created_room.json() == ROOM_RESPONSE
    assert client.post("/api/v1/course-programs", json=PROGRAM).json() == PROGRAM_RESPONSE

    assert client.get("/api/v1/rooms/LB204").json()["capacity"] == 20
    assert (
        client.get("/api/v1/course-programs/BSCS").json()["program_name"]
        == "BS Computer Science"
    )

    # Capacity is derived; patching room_type or expanded recomputes it.
    patched = client.patch("/api/v1/rooms/LB204", json={"expanded": True})
    assert patched.status_code == 200
    assert patched.json()["capacity"] == 25
    assert patched.json()["expanded"] is True

    patched2 = client.patch("/api/v1/rooms/LB204", json={"room_type": "lecture"})
    assert patched2.status_code == 200
    assert patched2.json()["capacity"] == 45
    assert patched2.json()["room_type"] == "lecture"

    assert client.delete("/api/v1/rooms/LB204").status_code == 204
    assert client.get("/api/v1/rooms/LB204").json()["is_active"] is False
    assert client.get("/api/v1/rooms").json()["items"] == []
    assert client.delete("/api/v1/course-programs/BSCS").status_code == 204
    assert client.get("/api/v1/course-programs/BSCS").json()["is_active"] is False
    assert client.get("/api/v1/course-programs").json()["items"] == []
    restored = client.patch("/api/v1/rooms/LB204", json={"is_active": True})
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True


def test_room_allowed_subjects_mirror_subject_allowed_rooms(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)
    client.post("/api/v1/subjects", json=SUBJECT)
    client.post("/api/v1/subjects", json={**SUBJECT, "subject_code": "CS102"})
    created = client.post(
        "/api/v1/rooms", json={**ROOM, "allowed_subject_codes": [" CS101 "]}
    )
    assert created.status_code == 201
    assert created.json()["allowed_subject_codes"] == ["CS101"]
    assert client.get("/api/v1/subjects/CS101").json()["allowed_room_codes"] == ["LB204"]

    # Editing either side shows on the other: they are the same rows.
    client.patch("/api/v1/subjects/CS102", json={"allowed_room_codes": ["LB204"]})
    assert client.get("/api/v1/rooms").json()["items"][0]["allowed_subject_codes"] == ["CS101", "CS102"]

    patched = client.patch("/api/v1/rooms/LB204", json={"allowed_subject_codes": ["CS102"]})
    assert patched.json()["allowed_subject_codes"] == ["CS102"]
    assert client.get("/api/v1/subjects/CS101").json()["allowed_room_codes"] == []

    # Other room fields leave the list alone; [] clears it.
    client.patch("/api/v1/rooms/LB204", json={"expanded": True})
    assert client.get("/api/v1/rooms/LB204").json()["allowed_subject_codes"] == ["CS102"]
    client.patch("/api/v1/rooms/LB204", json={"allowed_subject_codes": []})
    assert client.get("/api/v1/subjects/CS102").json()["allowed_room_codes"] == []

    unknown = client.patch("/api/v1/rooms/LB204", json={"allowed_subject_codes": ["NOPE1"]})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "UNKNOWN_SUBJECT"


def test_duplicate_subject_code_is_rejected(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/subjects", json=SUBJECT)

    duplicate = client.post("/api/v1/subjects", json=SUBJECT)

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE"


def test_patch_cannot_change_an_identifier(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/subjects", json=SUBJECT)

    patched = client.patch(
        "/api/v1/subjects/CS101", json={"subject_code": "CS999", "units": 5}
    )

    assert patched.status_code == 200
    assert patched.json()["subject_code"] == "CS101"
    assert client.get("/api/v1/subjects/CS999").status_code == 404


def test_subject_type_derives_duration_and_rejects_unknown_types(db_session: Session):
    client = make_api_client(db_session)

    created = client.post(
        "/api/v1/subjects",
        json={**SUBJECT, "subject_type": "LAB", "duration_minutes": 1},
    )
    assert created.status_code == 201
    assert created.json()["duration_minutes"] == 150

    patched = client.patch(
        "/api/v1/subjects/CS101",
        json={"subject_type": "LEC", "duration_minutes": 1},
    )
    assert patched.status_code == 200
    assert patched.json()["subject_type"] == "LEC"
    assert patched.json()["duration_minutes"] == 90

    rejected = client.post(
        "/api/v1/subjects",
        json={**SUBJECT, "subject_code": "CS999", "subject_type": "OTHER"},
    )
    assert rejected.status_code == 422


def test_deleting_a_referenced_subject_is_refused(db_session: Session):
    client = make_api_client(db_session)
    term = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    ).json()
    client.post("/api/v1/subjects", json=SUBJECT)
    client.post("/api/v1/course-programs", json=PROGRAM)
    block = BlockSection(
        term_id=term["term_id"],
        program_code="BSCS",
        year_level=1,
        section_letter="A",
    )
    offering = SubjectOffering(
        term_id=term["term_id"], subject_code="CS101", group_no=1
    )
    db_session.add_all([block, offering])
    db_session.flush()
    db_session.add(
        BlockSectionOffering(
            block_id=block.block_id,
            offering_id=offering.offering_id,
            term_id=block.term_id,
            subject_code="CS101",
        )
    )
    db_session.flush()

    # Retiring never touches the classes and offerings that name the subject.
    assert client.delete("/api/v1/subjects/CS101").status_code == 204
    assert db_session.get(SubjectOffering, offering.offering_id) is not None
    assert db_session.query(BlockSectionOffering).filter_by(
        subject_code="CS101"
    ).count() == 1


def test_capacity_is_derived_and_rejects_unknown_types(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)

    # Lecture room: base capacity 40.
    lec = client.post("/api/v1/rooms", json={
        "room_code": "LEC01", "building_name": "Lawton", "room_type": "lecture",
    })
    assert lec.status_code == 201
    assert lec.json()["capacity"] == 40
    assert lec.json()["expanded"] is False

    # Lecture room, expanded: 45.
    lec_exp = client.post("/api/v1/rooms", json={
        "room_code": "LEC02", "building_name": "Lawton", "room_type": "lecture", "expanded": True,
    })
    assert lec_exp.status_code == 201
    assert lec_exp.json()["capacity"] == 45

    # Laboratory room: base capacity 20.
    lab = client.post("/api/v1/rooms", json={
        "room_code": "LAB01", "building_name": "Lawton", "room_type": "laboratory",
    })
    assert lab.status_code == 201
    assert lab.json()["capacity"] == 20

    # Laboratory room, expanded: 25.
    lab_exp = client.post("/api/v1/rooms", json={
        "room_code": "LAB02", "building_name": "Lawton", "room_type": "laboratory", "expanded": True,
    })
    assert lab_exp.status_code == 201
    assert lab_exp.json()["capacity"] == 25

    # Unknown room type rejected.
    rejected = client.post("/api/v1/rooms", json={
        "room_code": "BAD01", "building_name": "Lawton", "room_type": "unknown",
    })
    assert rejected.status_code == 422

    # Patching expanded recomputes capacity.
    patched = client.patch("/api/v1/rooms/LEC01", json={"expanded": True})
    assert patched.json()["capacity"] == 45


def test_capacity_can_be_overridden_and_reset(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)

    created = client.post("/api/v1/rooms", json={
        "room_code": "LEC09", "building_name": "Lawton", "room_type": "lecture",
        "capacity": 32,
    })
    assert created.status_code == 201
    assert created.json()["capacity"] == 32

    # Edits that do not touch the seat count keep the override.
    assert client.delete("/api/v1/rooms/LEC09").status_code == 204
    restored = client.patch("/api/v1/rooms/LEC09", json={"is_active": True})
    assert restored.json()["capacity"] == 32

    overridden = client.patch("/api/v1/rooms/LEC09", json={"capacity": 50})
    assert overridden.json()["capacity"] == 50

    # Null resets to the derived value.
    reset = client.patch("/api/v1/rooms/LEC09", json={"capacity": None})
    assert reset.json()["capacity"] == 40

    for bad in (0, -1, 501):
        rejected = client.patch("/api/v1/rooms/LEC09", json={"capacity": bad})
        assert rejected.status_code == 422


@pytest.mark.parametrize("typed", ["LT 204", "LT-204", "LT LT204", "lt204"])
def test_a_new_room_code_is_stored_in_the_one_catalog_spelling(
    db_session: Session, typed: str
):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)

    created = client.post("/api/v1/rooms", json={**ROOM, "room_code": typed})
    assert created.status_code == 201
    assert created.json()["room_code"] == "LT204"
    # Any other spelling of the same room is the same room.
    again = client.post("/api/v1/rooms", json={**ROOM, "room_code": "LT 204"})
    assert again.status_code == 409


def test_catalog_codes_containing_a_slash_stay_manageable(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)
    room = {**ROOM, "room_code": "B 1/2"}
    assert client.post("/api/v1/rooms", json=room).status_code == 201

    url = "/api/v1/rooms/B%201%2F2"
    assert client.get(url).status_code == 200
    patched = client.patch(url, json={"expanded": True})
    assert patched.status_code == 200
    assert patched.json()["room_code"] == "B 1/2"
    assert client.delete(url).status_code == 204
    assert client.get(url).json()["is_active"] is False

    subject = {**SUBJECT, "subject_code": "CS 1/2"}
    assert client.post("/api/v1/subjects", json=subject).status_code == 201
    assert client.delete("/api/v1/subjects/CS%201%2F2").status_code == 204
    program = {**PROGRAM, "program_code": "BS/CS"}
    assert client.post("/api/v1/course-programs", json=program).status_code == 201
    assert client.delete("/api/v1/course-programs/BS%2FCS").status_code == 204


@pytest.mark.parametrize("collection", ["subjects", "rooms", "course-programs"])
def test_collection_trailing_slash_redirects_to_the_collection(
    db_session: Session, collection: str
):
    client = make_api_client(db_session)
    base = f"/api/v1/{collection}"
    redirected = client.get(f"{base}/?x=1", follow_redirects=False)
    assert redirected.status_code == 307
    assert redirected.headers["location"].endswith(f"{base}?x=1")
    assert client.get(f"{base}/").status_code == 200

    anonymous = make_client(db_session)
    assert anonymous.get(f"{base}/", follow_redirects=False).status_code in (401, 403)


def test_post_through_the_trailing_slash_redirect_still_creates(db_session: Session):
    client = make_api_client(db_session)
    created = client.post("/api/v1/subjects/", json=SUBJECT)
    assert created.status_code == 201
    assert client.get("/api/v1/subjects/CS101").status_code == 200


def test_subject_and_program_slash_codes_can_be_read_and_patched(db_session: Session):
    client = make_api_client(db_session)
    subject = {**SUBJECT, "subject_code": "CS 1/2"}
    client.post("/api/v1/subjects", json=subject)
    url = "/api/v1/subjects/CS%201%2F2"
    assert client.get(url).json()["subject_code"] == "CS 1/2"
    assert client.patch(url, json={"units": 4}).json()["units"] == 4

    program = {**PROGRAM, "program_code": "BS/CS"}
    client.post("/api/v1/course-programs", json=program)
    url = "/api/v1/course-programs/BS%2FCS"
    assert client.get(url).json()["program_code"] == "BS/CS"
    patched = client.patch(url, json={"program_name": "Renamed"})
    assert patched.json()["program_name"] == "Renamed"


def test_retired_room_cannot_be_chosen_but_an_existing_choice_is_kept(db_session: Session):
    client = make_api_client(db_session)
    client.post("/api/v1/buildings", json=BUILDING)
    client.post("/api/v1/rooms", json=ROOM)
    client.post("/api/v1/rooms", json={**ROOM, "room_code": "LB205"})
    assert client.post("/api/v1/subjects", json={**SUBJECT, "allowed_room_codes": ["LB204"]}).status_code == 201
    client.delete("/api/v1/rooms/LB204")
    client.delete("/api/v1/rooms/LB205")

    rejected = client.patch("/api/v1/subjects/CS101", json={"allowed_room_codes": ["LB204", "LB205"]})
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "INACTIVE_ROOM"

    kept = client.patch("/api/v1/subjects/CS101", json={"units": 2, "allowed_room_codes": ["LB204"]})
    assert kept.status_code == 200
    assert kept.json()["allowed_room_codes"] == ["LB204"]

    created = client.post(
        "/api/v1/subjects",
        json={**SUBJECT, "subject_code": "CS102", "allowed_room_codes": ["LB205"]},
    )
    assert created.status_code == 422
