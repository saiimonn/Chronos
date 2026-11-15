"""Master schedule Block locks, and conflicts raised by a roster change.

Approving a Block locks it: its week is read-only until unlocked, finalizing
waits for every Block to be locked again, and an edit that reaches a locked
partner Block (a combined LEC session or a paired LAB) is refused.
"""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    Faculty,
    Schedule,
    ScheduleRecord,
    Subject,
    SubjectOffering,
)
from app.models.schedule import ScheduleType
from app.tests.support.api import make_api_client
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    OTHER_CLASS_ID,
    TERM_ID,
    TIME_SLOT_ID,
    add_combined_block,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

OTHER_BLOCK_ID = UUID("00000000-0000-0000-0000-0000000000b2")
PLACEMENT = {
    "faculty_id": str(FACULTY_ID),
    "room_code": "<ROOM-1>",
    "meeting_days": "MW",
    "time_slot_id": str(TIME_SLOT_ID),
}


def _master(client) -> str:
    created = client.post("/api/v1/schedules/master", json={"term_id": str(TERM_ID)})
    assert created.status_code == 201, created.text
    return created.json()["schedule_id"]


def _approve(client, schedule_id: str, block_id, class_id, **overrides) -> dict:
    preview = client.post(
        f"/api/v1/schedules/{schedule_id}/blocks/{block_id}/preview",
        json={"assignments": [{"class_id": str(class_id), **PLACEMENT, **overrides}]},
    )
    assert preview.status_code == 200, preview.text
    return client.post(
        f"/api/v1/schedules/{schedule_id}/blocks/{block_id}/confirm",
        json={"preview_token": preview.json()["preview_token"]},
    )


def _record_id(body: dict, class_id) -> str:
    return next(
        item["record_id"]
        for item in body["schedule"]["assignments"]
        if item["class_id"] == str(class_id)
    )


def test_approving_locks_the_block_and_unlocking_opens_it_for_editing(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    approved = _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    assert approved.status_code == 200, approved.text
    assert approved.json()["schedule"]["unlocked_block_ids"] == []
    edit_url = (
        f"/api/v1/schedules/{schedule_id}/assignments/"
        f"{_record_id(approved.json(), CLASS_ID)}"
    )

    locked_edit = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert locked_edit.status_code == 409
    assert locked_edit.json()["code"] == "BLOCK_LOCKED"

    unlocked = client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["schedule"]["unlocked_block_ids"] == [str(BLOCK_ID)]

    edited = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert edited.status_code == 200, edited.text

    refused = client.post(f"/api/v1/schedules/{schedule_id}/finalize")
    assert refused.status_code == 409
    assert refused.json()["code"] == "BLOCKS_UNLOCKED"

    relocked = client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/lock")
    assert relocked.status_code == 200, relocked.text
    assert relocked.json()["schedule"]["unlocked_block_ids"] == []

    finalized = client.post(f"/api/v1/schedules/{schedule_id}/finalize")
    assert finalized.status_code == 200, finalized.text


def test_a_locked_block_is_not_replaced_until_unlocked_and_approval_relocks(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    assert _approve(client, schedule_id, BLOCK_ID, CLASS_ID).status_code == 200

    refused = _approve(client, schedule_id, BLOCK_ID, CLASS_ID, meeting_days="TTh")
    assert refused.status_code == 409
    assert refused.json()["code"] == "BLOCK_LOCKED"

    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")
    replaced = _approve(client, schedule_id, BLOCK_ID, CLASS_ID, meeting_days="TTh")
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["schedule"]["unlocked_block_ids"] == []
    assert replaced.json()["schedule"]["assignments"][0]["meeting_days"] == "TTh"


def test_clearing_needs_an_unlocked_block_and_withdraws_its_week(db_session: Session):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    clear_url = f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/clear"

    refused = client.post(clear_url)
    assert refused.status_code == 409
    assert refused.json()["code"] == "BLOCK_LOCKED"

    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")
    cleared = client.post(clear_url)
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["schedule"]["assignments"] == []
    assert body["schedule"]["unlocked_block_ids"] == []
    assert body["validation"]["assigned_class_count"] == 0


def test_lock_is_refused_while_the_block_has_a_conflict(db_session: Session):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()

    refused = client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/lock")

    assert refused.status_code == 409
    assert refused.json()["code"] == "BLOCK_HAS_CONFLICTS"
    assert refused.json()["details"]["conflicts"][0]["code"] == "FACULTY_INACTIVE"


def test_deactivating_a_teacher_flags_their_classes_until_finalized(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    assert client.post(f"/api/v1/schedules/{schedule_id}/finalize").status_code == 200

    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()
    # A finalized schedule keeps the past teacher it was approved with.
    finalized = client.post(f"/api/v1/schedules/{schedule_id}/validate").json()
    assert finalized["hard_conflict_count"] == 0

    db_session.get(Schedule, UUID(schedule_id)).finalized_at = None
    db_session.commit()
    building = client.post(f"/api/v1/schedules/{schedule_id}/validate").json()
    assert building["hard_conflict_count"] == 1
    conflict = building["conflicts"][0]
    assert conflict["code"] == "FACULTY_INACTIVE"
    assert conflict["class_ids"] == [str(CLASS_ID)]
    assert building["export_allowed"] is False


def test_a_proposal_keeping_a_deactivated_teacher_reports_the_conflict(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    schedule_id = _master(client)
    _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()

    preview = client.post(
        f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/preview",
        json={"assignments": [{"class_id": str(CLASS_ID), **PLACEMENT}]},
    )

    assert preview.status_code == 200, preview.text
    codes = [item["code"] for item in preview.json()["validation"]["conflicts"]]
    assert codes == ["FACULTY_INACTIVE"]


def test_editing_a_combined_session_needs_every_block_in_it_unlocked(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    add_combined_block(db_session)
    db_session.commit()
    client = make_api_client(db_session)
    schedule_id = _master(client)
    _approve(client, schedule_id, BLOCK_ID, CLASS_ID)
    both = _approve(client, schedule_id, OTHER_BLOCK_ID, OTHER_CLASS_ID)
    assert both.status_code == 200, both.text
    edit_url = (
        f"/api/v1/schedules/{schedule_id}/assignments/"
        f"{_record_id(both.json(), CLASS_ID)}"
    )
    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")

    refused = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert refused.status_code == 409
    assert refused.json()["code"] == "BLOCK_LOCKED"
    assert refused.json()["details"]["block_ids"] == [str(OTHER_BLOCK_ID)]

    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{OTHER_BLOCK_ID}/unlock")
    moved = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert moved.status_code == 200, moved.text
    assert {item["meeting_days"] for item in moved.json()["schedule"]["assignments"]} == {
        "TTh"
    }


def _seed_paired_labs(db: Session) -> None:
    """Blocks A and B of one cohort, each taking its own section of one LAB."""
    seed_scheduling_problem(db)
    db.get(Subject, "<SUBJECT-1>").subject_type = "LAB"
    db.add(
        BlockSection(
            block_id=OTHER_BLOCK_ID,
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="B",
            is_expanded=False,
        )
    )
    other_offering = UUID("00000000-0000-0000-0000-0000000000f2")
    db.add(
        SubjectOffering(
            offering_id=other_offering,
            term_id=TERM_ID,
            subject_code="<SUBJECT-1>",
            group_no=2,
        )
    )
    db.flush()
    db.add(
        BlockSectionOffering(
            class_id=OTHER_CLASS_ID,
            block_id=OTHER_BLOCK_ID,
            offering_id=other_offering,
            term_id=TERM_ID,
            subject_code="<SUBJECT-1>",
        )
    )
    schedule = Schedule(term_id=TERM_ID, schedule_type=ScheduleType.MASTER.value)
    db.add(schedule)
    db.flush()
    for class_id in (CLASS_ID, OTHER_CLASS_ID):
        db.add(
            ScheduleRecord(
                schedule_id=schedule.schedule_id,
                term_id=TERM_ID,
                faculty_id=FACULTY_ID,
                subject_code="<SUBJECT-1>",
                class_id=class_id,
                time_slot_id=TIME_SLOT_ID,
                room_code="<ROOM-1>",
                meeting_days="MW",
                time_slot="<TIME-SLOT-1>",
                is_historical=False,
            )
        )
    db.commit()


def test_moving_a_paired_lab_moves_its_partner_once_both_are_unlocked(
    db_session: Session,
):
    _seed_paired_labs(db_session)
    client = make_api_client(db_session)
    schedule = client.get(f"/api/v1/schedules/master?term_id={TERM_ID}").json()
    schedule_id = schedule["schedule_id"]
    record_id = next(
        item["record_id"]
        for item in schedule["assignments"]
        if item["class_id"] == str(CLASS_ID)
    )
    edit_url = f"/api/v1/schedules/{schedule_id}/assignments/{record_id}"
    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{BLOCK_ID}/unlock")

    refused = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert refused.status_code == 409
    assert refused.json()["details"]["block_ids"] == [str(OTHER_BLOCK_ID)]

    client.post(f"/api/v1/schedules/{schedule_id}/blocks/{OTHER_BLOCK_ID}/unlock")
    moved = client.patch(edit_url, json={"meeting_days": "TTh"})
    assert moved.status_code == 200, moved.text
    days = {
        item["class_id"]: item["meeting_days"]
        for item in moved.json()["schedule"]["assignments"]
    }
    assert days == {str(CLASS_ID): "TTh", str(OTHER_CLASS_ID): "TTh"}
    codes = {item["code"] for item in moved.json()["validation"]["conflicts"]}
    assert "PAIRED_LAB_MISMATCH" not in codes
