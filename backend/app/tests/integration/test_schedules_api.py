import re
from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from uuid import UUID

import pytest
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import Assignment, CandidateSchedule
from sqlalchemy import select

from app.models import AppUser, AuditEvent, ExecutionJob, Faculty, Room, Schedule, ScheduleRecord, Term, TimeSlot, UserRole
from app.models.execution_job import JobStatus, JobType
from app.services.schedules import EXPORT_HEADERS, persist_candidate
from app.tests.support.api import make_api_client, make_client
from app.tests.support.pdf import capture_pdf_pages
from app.tests.support.scheduling import (
    BLOCK_ID,
    FACULTY_ID,
    OFFERING_ID,
    OTHER_CLASS_ID,
    SCHEDULE_ID,
    CLASS_ID,
    TERM_ID,
    TIME_SLOT_ID,
    add_combined_block,
    seed_scheduling_problem,
)


pytestmark = pytest.mark.postgres


def _persist_complete_schedule(db: Session):
    seed_scheduling_problem(db)
    return persist_candidate(
        db,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(
                    class_id=CLASS_ID,
                    faculty_id=FACULTY_ID,
                    time_slot_id=TIME_SLOT_ID,
                    room_code="<ROOM-1>",
                    meeting_days="MW",
                ),
            ),
        ),
    )


def test_complete_schedule_validates_finalizes_and_exports_both_formats(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)

    validation = client.post(f"/api/v1/schedules/{schedule.schedule_id}/validate")
    blocked = client.get(f"/api/v1/schedules/{schedule.schedule_id}/export")
    finalized = client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize")
    csv_export = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export?format=csv"
    )
    xlsx_export = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export?format=xlsx"
    )

    assert validation.status_code == 200
    assert validation.json() == {
        "required_class_count": 1,
        "assigned_class_count": 1,
        "completeness_percentage": 100.0,
        "unassigned_class_ids": [],
        "hard_conflict_count": 0,
        "conflicts": [],
        "export_allowed": True,
    }
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SCHEDULE_NOT_FINALIZED"
    assert finalized.status_code == 200
    assert finalized.json()["schedule"]["finalized_at"] is not None

    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    csv_text = csv_export.content.decode("utf-8-sig")
    assert "academic_year,term_number,subject_code" in csv_text
    assert "<YEAR-1>,1,<SUBJECT-1>" in csv_text

    assert xlsx_export.status_code == 200
    workbook = load_workbook(BytesIO(xlsx_export.content), read_only=True)
    rows = list(workbook["Schedule"].values)
    assert rows[0][:3] == ("academic_year", "term_number", "subject_code")
    assert rows[1][:3] == ("<YEAR-1>", 1, "<SUBJECT-1>")


def test_assignments_round_trip_block_and_offering_provenance(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)

    read = client.get(f"/api/v1/schedules/{schedule.schedule_id}")

    assert read.status_code == 200
    assignment = read.json()["assignments"][0]
    assert assignment["class_id"] == str(CLASS_ID)
    assert assignment["offering_id"] == str(OFFERING_ID)
    assert assignment["block_id"] == str(BLOCK_ID)


def test_timetable_projection_returns_labels_and_canonical_intervals(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    schedule.records[0].time_slot = "<STALE-LABEL>"
    db_session.commit()
    client = make_api_client(db_session)

    response = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable?term_id={TERM_ID}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schedule_id"] == str(SCHEDULE_ID)
    assert body["term_id"] == str(TERM_ID)
    assert body["schedule_type"] == "routine"
    assert body["finalization_state"] == "candidate"
    assert body["calendar"] == {
        "teaching_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
        "opening_time": "07:30:00",
        "closing_time": "21:00:00",
        "interval_minutes": 30,
    }
    assert body["assignments"] == [
        {
            "record_id": str(schedule.records[0].record_id),
            "offering_id": str(OFFERING_ID),
            "subject_code": "<SUBJECT-1>",
            "subject_title": "<SUBJECT-TITLE-1>",
            "group_label": "<SUBJECT-1> G1",
            "block_id": str(BLOCK_ID),
            "block_name": "<PROGRAM-1>-1A",
            "program_code": "<PROGRAM-1>",
            "year_level": 1,
            "faculty_id": str(FACULTY_ID),
            "faculty_name": "<FACULTY-1>",
            "room_code": "<ROOM-1>",
            "room_type": "lecture",
            "room_capacity": 40,
            "meeting_days": "MW",
            "time_slot_id": str(TIME_SLOT_ID),
            "time_slot_label": "<TIME-SLOT-1>",
            "start_time": "08:00:00",
            "end_time": "09:30:00",
        }
    ]
    db_session.refresh(schedule.records[0])
    assert schedule.records[0].time_slot == "<STALE-LABEL>"

    assert client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/finalize"
    ).status_code == 200
    finalized = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable?term_id={TERM_ID}"
    )
    assert finalized.json()["finalization_state"] == "finalized"
    assert finalized.json()["finalized_at"] is not None


def test_timetable_projection_validates_and_applies_filters(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    db_session.add(
        Room(
            room_code="<ROOM-2>",
            building_name="<BUILDING-1>",
            capacity=40,
            room_type="lecture",
            expanded=False,
        )
    )
    db_session.commit()
    client = make_api_client(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/timetable?term_id={TERM_ID}"

    by_block = client.get(f"{base}&block_id={BLOCK_ID}")
    by_room = client.get(f"{base}&room_code=%3CROOM-1%3E")
    no_match = client.get(
        f"{base}&block_id={BLOCK_ID}&room_code=%3CROOM-2%3E"
    )
    unknown_block = client.get(
        f"{base}&block_id=00000000-0000-0000-0000-000000000099"
    )
    unknown_room = client.get(f"{base}&room_code=%3CUNKNOWN%3E")
    unknown_schedule = client.get(
        "/api/v1/schedules/00000000-0000-0000-0000-000000000099/timetable"
        f"?term_id={TERM_ID}"
    )
    unknown_term = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable"
        "?term_id=00000000-0000-0000-0000-000000000099"
    )

    assert len(by_block.json()["assignments"]) == 1
    assert len(by_room.json()["assignments"]) == 1
    assert no_match.json()["assignments"] == []
    for response in (unknown_block, unknown_room, unknown_schedule, unknown_term):
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
        assert response.json()["details"] == {}
        assert "correlation_id" in response.json()


def test_timetable_projection_filters_by_faculty(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    other = _add_other_faculty(db_session)
    client = make_api_client(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/timetable?term_id={TERM_ID}"

    mine = client.get(f"{base}&faculty_id={FACULTY_ID}")
    idle = client.get(f"{base}&faculty_id={other}")
    # Filters combine with AND, like block_id and room_code.
    combined = client.get(
        f"{base}&faculty_id={FACULTY_ID}&block_id={BLOCK_ID}&room_code=%3CROOM-1%3E"
    )
    unknown = client.get(f"{base}&faculty_id=00000000-0000-0000-0000-000000000099")
    malformed = client.get(f"{base}&faculty_id=not-a-uuid")

    assert len(mine.json()["assignments"]) == 1
    assert idle.json()["assignments"] == []
    assert len(combined.json()["assignments"]) == 1
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "NOT_FOUND"
    assert malformed.status_code == 422


def test_timetable_projection_requires_schedule_to_belong_to_requested_term(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    other_term_id = UUID("00000000-0000-0000-0000-000000000099")
    db_session.add(
        Term(
            term_id=other_term_id,
            academic_year="<YEAR-2>",
            term_number=2,
            is_active=False,
        )
    )
    db_session.commit()
    client = make_api_client(db_session)

    response = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable?term_id={other_term_id}"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_timetable_projection_rejects_an_inactive_term(db_session: Session):
    term_id = UUID("00000000-0000-0000-0000-000000000099")
    schedule_id = UUID("00000000-0000-0000-0000-000000000098")
    db_session.add(
        Term(
            term_id=term_id,
            academic_year="<YEAR-2>",
            term_number=2,
            is_active=False,
        )
    )
    db_session.add(Schedule(schedule_id=schedule_id, term_id=term_id))
    db_session.commit()
    client = make_api_client(db_session)

    response = client.get(
        f"/api/v1/schedules/{schedule_id}/timetable?term_id={term_id}"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_NOT_ACTIVE"


def test_timetable_projection_sanitizes_malformed_filters(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)

    response = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable"
        f"?term_id={TERM_ID}&block_id=not-a-uuid"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "correlation_id" in response.json()

    blank_room = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/timetable"
        f"?term_id={TERM_ID}&room_code=%20"
    )
    assert blank_room.status_code == 422
    assert blank_room.json()["code"] == "VALIDATION_ERROR"


def test_export_carries_block_and_offering_labels(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize")

    export = client.get(f"/api/v1/schedules/{schedule.schedule_id}/export?format=csv")

    assert export.status_code == 200
    header, row = export.content.decode("utf-8-sig").splitlines()[:2]
    assert header.split(",") == list(EXPORT_HEADERS)
    fields = dict(zip(EXPORT_HEADERS, row.split(",")))
    # Labels a reader needs, carried as plain values rather than ORM objects.
    assert fields["group_label"] == "<SUBJECT-1> G1"
    assert fields["block_name"] == "<PROGRAM-1>-1A"
    assert fields["program_code"] == "<PROGRAM-1>"
    assert fields["year_level"] == "1"
    assert fields["section_letter"] == "A"
    assert fields["subject_title"] == "<SUBJECT-TITLE-1>"


def _persist_finalized_combined_schedule(db: Session):
    seed_scheduling_problem(db)
    add_combined_block(db)
    db.add(
        Room(
            room_code="<ROOM-2>",
            building_name="<BUILDING-1>",
            capacity=40,
            room_type="lecture",
            expanded=False,
        )
    )
    db.commit()
    placement = {
        "faculty_id": FACULTY_ID,
        "time_slot_id": TIME_SLOT_ID,
        "room_code": "<ROOM-1>",
        "meeting_days": "MW",
    }
    schedule = persist_candidate(
        db,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(class_id=CLASS_ID, **placement),
                Assignment(class_id=OTHER_CLASS_ID, **placement),
            ),
        ),
    )
    client = make_api_client(db)
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200
    return schedule, client


def _csv_rows(response) -> list[dict[str, str]]:
    header, *rows = response.content.decode("utf-8-sig").splitlines()
    assert header.split(",") == list(EXPORT_HEADERS)
    return [dict(zip(EXPORT_HEADERS, row.split(","))) for row in rows]


def test_export_follows_the_timetable_block_and_room_filters(db_session: Session):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/export?format=csv"

    whole = client.get(base)
    by_block = client.get(f"{base}&block_id={BLOCK_ID}")
    by_room = client.get(f"{base}&room_code=%3CROOM-1%3E")
    empty_room = client.get(f"{base}&room_code=%3CROOM-2%3E")
    xlsx_by_block = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export"
        f"?format=xlsx&block_id={BLOCK_ID}"
    )

    assert sorted(row["block_name"] for row in _csv_rows(whole)) == [
        "<PROGRAM-1>-1A",
        "<PROGRAM-1>-1B",
    ]
    assert [row["block_name"] for row in _csv_rows(by_block)] == ["<PROGRAM-1>-1A"]
    assert len(_csv_rows(by_room)) == 2
    assert _csv_rows(empty_room) == []
    assert re.search(
        r'filename="chronos_YEAR-1_term1_\d{8}-\d{4}\.csv"',
        whole.headers["content-disposition"],
    )
    assert "_block_PROGRAM-1_-1A.csv" in by_block.headers["content-disposition"]
    assert "_room_ROOM-1.csv" in by_room.headers["content-disposition"]
    workbook = load_workbook(BytesIO(xlsx_by_block.content), read_only=True)
    assert len(list(workbook.active.iter_rows(min_row=2))) == 1


def test_pdf_export_follows_filters_and_carries_metadata(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/export?format=pdf"
    saved = capture_pdf_pages(monkeypatch)

    whole = client.get(base)
    by_block = client.get(f"{base}&block_id={BLOCK_ID}&room_code=%3CROOM-1%3E")
    whole_page, block_page = (page[0].splitlines() for page in saved)

    for response in (whole, by_block):
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
    assert re.search(
        r"chronos_YEAR-1_term1_\d{8}-\d{4}\.pdf",
        whole.headers["content-disposition"],
    )
    assert "_block_PROGRAM-1_-1A_room_ROOM-1.pdf" in by_block.headers[
        "content-disposition"
    ]
    # Drawn on page 1.
    assert whole_page[:2] == [
        "Term: <YEAR-1> Term 1",
        f"Schedule: {SCHEDULE_ID} (routine)",
    ]
    assert whole_page[2].startswith("Finalized at: ")
    assert whole_page[3:5] == ["Scope: All blocks; all rooms", "Assignments: 2"]
    assert whole_page[5].startswith("Generated at: ")
    assert whole_page[6] == "Page: 1 of 1"
    assert block_page[3:5] == [
        "Scope: Block <PROGRAM-1>-1A; room <ROOM-1>",
        "Assignments: 1",
    ]
    # Also in the document Info /Subject, where PDF string syntax escapes "(".
    assert f"Schedule: {SCHEDULE_ID} \\(routine\\)".encode() in whole.content
    assert b"Scope: Block <PROGRAM-1>-1A; room <ROOM-1>" in by_block.content


def test_pdf_export_is_refused_by_the_same_gate(db_session: Session):
    seed_scheduling_problem(db_session)
    db_session.add(Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID))
    db_session.commit()
    client = make_api_client(db_session)
    url = f"/api/v1/schedules/{SCHEDULE_ID}/export?format=pdf"

    not_finalized = client.get(url)
    unauthenticated = make_client(db_session).get(url)

    assert not_finalized.status_code == 409
    assert not_finalized.json()["code"] == "SCHEDULE_NOT_FINALIZED"
    assert unauthenticated.status_code == 401


def test_pdf_export_revalidates_the_whole_schedule(db_session: Session):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    room = db_session.get(Room, "<ROOM-1>")
    room.capacity = 1
    db_session.commit()

    response = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export"
        "?format=pdf&room_code=%3CROOM-2%3E"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_NOT_EXPORTABLE"


def test_filtered_export_still_validates_the_whole_schedule(db_session: Session):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    room = db_session.get(Room, "<ROOM-1>")
    room.capacity = 1
    db_session.commit()

    # The empty room holds nothing in conflict, but the schedule as a whole does.
    response = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export?room_code=%3CROOM-2%3E"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_NOT_EXPORTABLE"


def test_filtered_export_rejects_unknown_and_malformed_filters(db_session: Session):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/export"

    unknown_block = client.get(
        f"{base}?block_id=00000000-0000-0000-0000-000000000099"
    )
    unknown_room = client.get(f"{base}?room_code=%3CUNKNOWN%3E")
    malformed_block = client.get(f"{base}?block_id=not-a-uuid")
    blank_room = client.get(f"{base}?room_code=%20")

    for response in (unknown_block, unknown_room):
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
    for response in (malformed_block, blank_room):
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_export_filters_by_faculty(db_session: Session):
    schedule, client = _persist_finalized_combined_schedule(db_session)
    other = _add_other_faculty(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}/export?format=csv"

    mine = client.get(f"{base}&faculty_id={FACULTY_ID}")
    idle = client.get(f"{base}&faculty_id={other}")
    combined = client.get(f"{base}&faculty_id={FACULTY_ID}&block_id={BLOCK_ID}")
    pdf = client.get(
        f"/api/v1/schedules/{schedule.schedule_id}/export"
        f"?format=pdf&faculty_id={FACULTY_ID}"
    )
    unknown = client.get(f"{base}&faculty_id=00000000-0000-0000-0000-000000000099")

    assert len(_csv_rows(mine)) == 2
    assert _csv_rows(idle) == []
    assert [r["block_name"] for r in _csv_rows(combined)] == ["<PROGRAM-1>-1A"]
    assert "_faculty_" in mine.headers["content-disposition"]
    assert b"faculty " in pdf.content
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "NOT_FOUND"


def _add_other_faculty(db: Session) -> UUID:
    other = UUID("00000000-0000-0000-0000-0000000000f2")
    db.add(
        Faculty(
            faculty_id=other,
            full_name="<FACULTY-2>",
            is_full_time=True,
            availability_mask="1" * 255,
        )
    )
    db.commit()
    return other


def test_master_schedule_has_a_distinct_type_and_one_per_term(db_session: Session):
    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)

    created = client.post("/api/v1/schedules/master", json={"term_id": str(TERM_ID)})
    read = client.get(f"/api/v1/schedules/master?term_id={TERM_ID}")
    listed = client.get(f"/api/v1/schedules?term_id={TERM_ID}&schedule_type=master")
    duplicate = client.post("/api/v1/schedules/master", json={"term_id": str(TERM_ID)})

    assert created.status_code == 201
    assert created.json()["schedule_type"] == "master"
    assert read.status_code == 200
    assert read.json()["schedule_id"] == created.json()["schedule_id"]
    assert listed.json()["items"] == [created.json()]
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "MASTER_SCHEDULE_EXISTS"


def test_incomplete_schedule_cannot_finalize(db_session: Session):
    seed_scheduling_problem(db_session)
    schedule = Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID)
    db_session.add(schedule)
    db_session.commit()
    client = make_api_client(db_session)

    response = client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SCHEDULE_NOT_EXPORTABLE"
    assert body["details"]["assigned_class_count"] == 0
    assert body["details"]["unassigned_class_ids"] == [str(CLASS_ID)]


def test_manual_edit_validates_and_rejects_unavailable_faculty(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    assignment = schedule.records[0]
    client = make_api_client(db_session)

    valid = client.patch(
        f"/api/v1/schedules/{schedule.schedule_id}/assignments/{assignment.record_id}",
        json={"meeting_days": "MW"},
    )
    faculty = db_session.get(Faculty, FACULTY_ID)
    faculty.availability_mask = "0"
    db_session.commit()
    rejected = client.patch(
        f"/api/v1/schedules/{schedule.schedule_id}/assignments/{assignment.record_id}",
        json={"meeting_days": "MW"},
    )

    assert valid.status_code == 200
    assert valid.json()["schedule"]["assignments"][0]["meeting_days"] == "MW"
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "FACULTY_UNAVAILABLE"
    db_session.refresh(assignment)
    assert assignment.meeting_days == "MW"


def test_a_retired_teachers_stale_mask_is_named_on_validate(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    teacher = db_session.get(Faculty, FACULTY_ID)
    teacher.is_active = False
    teacher.availability_mask = "11"  # saved for an older, longer slot grid
    db_session.commit()

    response = make_api_client(db_session).post(
        f"/api/v1/schedules/{schedule.schedule_id}/validate"
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "FACULTY_MASK_STALE"
    assert teacher.full_name in body["message"]
    assert body["details"]["faculty_id"] == str(FACULTY_ID)


def test_manual_edit_keeps_but_never_newly_assigns_an_inactive_teacher(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    assignment = schedule.records[0]
    retired = Faculty(
        full_name="<RETIRED>", is_full_time=False, is_active=False, availability_mask=""
    )
    db_session.add(retired)
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()
    client = make_api_client(db_session)
    url = f"/api/v1/schedules/{schedule.schedule_id}/assignments/{assignment.record_id}"

    # The assigned teacher retired: their class can still be edited in place.
    kept = client.patch(url, json={"faculty_id": str(FACULTY_ID), "meeting_days": "MW"})
    refused = client.patch(url, json={"faculty_id": str(retired.faculty_id)})

    assert kept.status_code == 200, kept.text
    assert refused.status_code == 409
    assert refused.json()["code"] == "FACULTY_INACTIVE"
    db_session.refresh(assignment)
    assert assignment.faculty_id == FACULTY_ID


def test_manual_edit_never_copies_an_inactive_teacher_onto_a_peer_block(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    add_combined_block(db_session)
    other = Faculty(full_name="<OTHER>", is_full_time=True, availability_mask="")
    db_session.add(other)
    db_session.flush()
    placement = {"time_slot_id": TIME_SLOT_ID, "room_code": "<ROOM-1>", "meeting_days": "MW"}
    # The two Blocks of one offering have drifted apart: A keeps the retired
    # teacher, B has someone else.
    schedule = persist_candidate(
        db_session,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(class_id=CLASS_ID, faculty_id=FACULTY_ID, **placement),
                Assignment(class_id=OTHER_CLASS_ID, faculty_id=other.faculty_id, **placement),
            ),
        ),
    )
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()
    record = next(item for item in schedule.records if item.class_id == CLASS_ID)
    peer = next(item for item in schedule.records if item.class_id == OTHER_CLASS_ID)

    response = make_api_client(db_session).patch(
        f"/api/v1/schedules/{SCHEDULE_ID}/assignments/{record.record_id}",
        json={"faculty_id": str(FACULTY_ID), "meeting_days": "MW"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "FACULTY_INACTIVE"
    db_session.refresh(peer)
    assert peer.faculty_id == other.faculty_id


def test_manual_edit_moves_every_block_of_a_combined_offering(db_session: Session):
    seed_scheduling_problem(db_session)
    add_combined_block(db_session)
    placement = {
        "faculty_id": FACULTY_ID,
        "time_slot_id": TIME_SLOT_ID,
        "room_code": "<ROOM-1>",
        "meeting_days": "MW",
    }
    schedule = persist_candidate(
        db_session,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(class_id=CLASS_ID, **placement),
                Assignment(class_id=OTHER_CLASS_ID, **placement),
            ),
        ),
    )
    record = next(item for item in schedule.records if item.class_id == CLASS_ID)
    client = make_api_client(db_session)

    response = client.patch(
        f"/api/v1/schedules/{SCHEDULE_ID}/assignments/{record.record_id}",
        json={"meeting_days": "MW"},
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["meeting_days"] for item in body["schedule"]["assignments"]} == {
        "MW"
    }
    assert body["validation"]["hard_conflict_count"] == 0


def test_manual_edit_refuses_a_slot_an_offering_peer_faculty_cannot_take(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    add_combined_block(db_session)
    afternoon_id = UUID("00000000-0000-0000-0000-0000000000d4")
    morning_only_id = UUID("00000000-0000-0000-0000-0000000000f2")
    db_session.add(
        TimeSlot(
            time_slot_id=afternoon_id,
            term_id=TERM_ID,
            label="<TIME-SLOT-2>",
            start_time=time(13),
            end_time=time(14, 30),
            ordinal=1,
        )
    )
    db_session.get(Faculty, FACULTY_ID).availability_mask = "11"
    db_session.add(
        Faculty(
            faculty_id=morning_only_id,
            full_name="<FACULTY-2>",
            is_full_time=False,
            availability_mask="10",
        )
    )
    db_session.flush()
    # A legacy split session: each Block kept its own teacher.
    placement = {
        "time_slot_id": TIME_SLOT_ID,
        "room_code": "<ROOM-1>",
        "meeting_days": "MW",
    }
    schedule = persist_candidate(
        db_session,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(class_id=CLASS_ID, faculty_id=FACULTY_ID, **placement),
                Assignment(
                    class_id=OTHER_CLASS_ID, faculty_id=morning_only_id, **placement
                ),
            ),
        ),
    )
    record = next(item for item in schedule.records if item.class_id == CLASS_ID)
    client = make_api_client(db_session)

    response = client.patch(
        f"/api/v1/schedules/{SCHEDULE_ID}/assignments/{record.record_id}",
        json={"time_slot_id": str(afternoon_id)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "FACULTY_UNAVAILABLE"
    assert response.json()["details"]["faculty_id"] == str(morning_only_id)
    db_session.expire_all()
    for item in db_session.get(Schedule, SCHEDULE_ID).records:
        assert (item.time_slot_id, item.time_slot) == (TIME_SLOT_ID, "<TIME-SLOT-1>")


def test_export_revalidates_current_catalog_after_finalization(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200

    room = db_session.get(Room, "<ROOM-1>")
    room.capacity = 1
    db_session.commit()
    response = client.get(f"/api/v1/schedules/{schedule.schedule_id}/export")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SCHEDULE_NOT_EXPORTABLE"
    assert body["details"]["hard_conflict_count"] == 1
    assert body["details"]["conflicts"][0]["code"] == "ROOM_CAPACITY_EXCEEDED"


def test_export_rejects_a_finalized_schedule_with_a_legacy_duration(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200

    slot = db_session.get(TimeSlot, TIME_SLOT_ID)
    assert slot is not None
    slot.end_time = time(9)
    db_session.commit()
    response = client.get(f"/api/v1/schedules/{schedule.schedule_id}/export")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SCHEDULE_NOT_EXPORTABLE"
    assert body["details"]["hard_conflict_count"] == 1
    assert body["details"]["conflicts"][0]["code"] == "SUBJECT_DURATION_MISMATCH"


def test_sunday_practicum_is_editable_but_mixed_patterns_are_refused(
    db_session: Session,
):
    """A standalone Su meeting (practicum) saves; MSu is not a pattern."""
    schedule = _persist_complete_schedule(db_session)
    assignment = schedule.records[0]
    client = make_api_client(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}"

    edited = client.patch(
        f"{base}/assignments/{assignment.record_id}", json={"meeting_days": "MSu"}
    )
    db_session.refresh(assignment)
    assert edited.status_code == 409
    assert edited.json()["code"] == "INVALID_SCHEDULE_DATA"
    assert assignment.meeting_days == "MW"

    practicum = client.patch(
        f"{base}/assignments/{assignment.record_id}", json={"meeting_days": "Su"}
    )
    assert practicum.status_code == 200
    db_session.refresh(assignment)
    assert assignment.meeting_days == "Su"
    validated = client.post(f"{base}/validate")
    assert validated.status_code == 200
    assert "OUTSIDE_TEACHING_CALENDAR" not in [
        item["code"] for item in validated.json()["conflicts"]
    ]
    assert client.post(f"{base}/finalize").status_code == 200
    assert client.get(f"{base}/export?format=csv").status_code == 200


def test_finalized_schedule_cannot_be_edited(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    assignment = schedule.records[0]
    client = make_api_client(db_session)
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200

    response = client.patch(
        f"/api/v1/schedules/{schedule.schedule_id}/assignments/{assignment.record_id}",
        json={"meeting_days": "MW"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_FINALIZED"


def test_schedule_routes_require_authentication(db_session: Session):
    client = make_client(db_session)

    response = client.get("/api/v1/schedules")

    assert response.status_code == 401


def test_timetable_projection_requires_authentication(db_session: Session):
    client = make_client(db_session)

    response = client.get(
        f"/api/v1/schedules/{SCHEDULE_ID}/timetable?term_id={TERM_ID}"
    )

    assert response.status_code == 401


def test_preview_and_confirm_block_replacement_workflow(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)

    # Preview modifying the class assignment in BLOCK_ID
    preview_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview",
        json={
            "assignments": [
                {
                    "class_id": str(CLASS_ID),
                    "faculty_id": str(FACULTY_ID),
                    "room_code": "<ROOM-1>",
                    "meeting_days": "TTh",
                    "time_slot_id": str(TIME_SLOT_ID),
                }
            ]
        },
    )
    assert preview_res.status_code == 200
    preview_data = preview_res.json()
    assert preview_data["schedule_id"] == str(schedule.schedule_id)
    assert preview_data["block_id"] == str(BLOCK_ID)
    assert preview_data["preview_token"]
    assert preview_data["validation"]["completeness_percentage"] == 100.0
    assert preview_data["validation"]["hard_conflict_count"] == 0
    assert len(preview_data["changed_values"]) == 1
    assert preview_data["changed_values"][0]["status"] == "modified"
    assert "meeting_days" in preview_data["changed_values"][0]["changed_fields"]

    # Ensure DB schedule was not modified by preview
    db_session.refresh(schedule.records[0])
    assert schedule.records[0].meeting_days == "MW"

    # Confirm replacement using preview_token
    confirm_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/confirm",
        json={"preview_token": preview_data["preview_token"]},
    )
    assert confirm_res.status_code == 200
    confirm_data = confirm_res.json()
    assert confirm_data["schedule"]["schedule_id"] == str(schedule.schedule_id)
    assert confirm_data["validation"]["completeness_percentage"] == 100.0
    assert len(confirm_data["schedule"]["assignments"]) == 1
    assert confirm_data["schedule"]["assignments"][0]["meeting_days"] == "TTh"

    # Verify DB schedule now has the updated assignment
    db_session.expire_all()
    reloaded = client.get(f"/api/v1/schedules/{schedule.schedule_id}").json()
    assert reloaded["assignments"][0]["meeting_days"] == "TTh"


def test_handwritten_block_preview_keeps_but_never_newly_assigns_an_inactive_teacher(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    retired = Faculty(
        full_name="<RETIRED>", is_full_time=False, is_active=False, availability_mask=""
    )
    db_session.add(retired)
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()
    client = make_api_client(db_session)
    url = f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview"
    proposal = {
        "class_id": str(CLASS_ID),
        "room_code": "<ROOM-1>",
        "meeting_days": "MW",
        "time_slot_id": str(TIME_SLOT_ID),
    }

    kept = client.post(
        url, json={"assignments": [{**proposal, "faculty_id": str(FACULTY_ID)}]}
    )
    refused = client.post(
        url, json={"assignments": [{**proposal, "faculty_id": str(retired.faculty_id)}]}
    )

    assert kept.status_code == 200, kept.text
    assert refused.status_code == 409
    assert refused.json()["code"] == "FACULTY_INACTIVE"


def test_block_preview_keeps_but_never_newly_uses_a_retired_room(db_session: Session):
    """CHR-240: retired rooms are guarded on preview, as on the approved-edit path."""
    schedule = _persist_complete_schedule(db_session)
    seeded = db_session.get(Room, "<ROOM-1>")
    db_session.add(
        Room(
            room_code="<ROOM-RETIRED>",
            building_name=seeded.building_name,
            capacity=seeded.capacity,
            room_type=seeded.room_type,
            expanded=seeded.expanded,
            is_active=False,
        )
    )
    # The class already meets in <ROOM-1>; retiring it must not strand that.
    seeded.is_active = False
    db_session.commit()
    client = make_api_client(db_session)
    url = f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview"
    proposal = {
        "class_id": str(CLASS_ID),
        "faculty_id": str(FACULTY_ID),
        "meeting_days": "MW",
        "time_slot_id": str(TIME_SLOT_ID),
    }

    kept = client.post(url, json={"assignments": [{**proposal, "room_code": "<ROOM-1>"}]})
    refused = client.post(
        url, json={"assignments": [{**proposal, "room_code": "<ROOM-RETIRED>"}]}
    )

    assert kept.status_code == 200, kept.text
    assert refused.status_code == 409
    assert refused.json()["code"] == "ROOM_INACTIVE"


def _succeeded_block_job(db_session: Session, faculty_id) -> ExecutionJob:
    from app.repositories.scheduling import SQLAlchemyBlockGenerationContextProvider
    from app.services.jobs.codecs import encode_block_generation_context

    context = SQLAlchemyBlockGenerationContextProvider(
        db_session
    ).get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)
    # Later than the server-set created_at, as the lifecycle checks require.
    ran_at = datetime.now(UTC) + timedelta(seconds=5)
    job = ExecutionJob(
        job_type=JobType.GA.value,
        status=JobStatus.SUCCEEDED.value,
        started_at=ran_at,
        finished_at=ran_at,
        request={"block_context": encode_block_generation_context(context)},
        result={
            "candidate": {
                "assignments": [
                    {
                        "class_id": str(CLASS_ID),
                        "faculty_id": str(faculty_id),
                        "room_code": "<ROOM-1>",
                        "meeting_days": "MW",
                        "time_slot_id": str(TIME_SLOT_ID),
                    }
                ]
            }
        },
        timeout_seconds=60,
        max_attempts=1,
        requested_by=db_session.scalar(select(AppUser.user_id)),
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_block_job_result_cannot_assign_a_teacher_deactivated_after_the_run(
    db_session: Session,
):
    _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    newcomer = Faculty(full_name="<NEWCOMER>", is_full_time=True, availability_mask="")
    db_session.add(newcomer)
    db_session.flush()
    # Active when the run was submitted, so the GA could draw them...
    job = _succeeded_block_job(db_session, newcomer.faculty_id)
    # ...then marked as no longer teaching before the result was applied.
    newcomer.is_active = False
    db_session.commit()

    response = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/preview",
        json={"job_id": str(job.job_id)},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "FACULTY_INACTIVE"


def _seed_combined_session(db_session: Session) -> Faculty:
    """CLASS_ID (Block A) and OTHER_CLASS_ID (Block B) share one offering.

    Returns a second, active teacher.
    """
    seed_scheduling_problem(db_session)
    add_combined_block(db_session)
    other = Faculty(full_name="<OTHER>", is_full_time=True, availability_mask="")
    db_session.add(other)
    db_session.flush()
    return other


def _persist_session(db_session: Session, block_a_teacher, block_b_teacher):
    placement = {"time_slot_id": TIME_SLOT_ID, "room_code": "<ROOM-1>", "meeting_days": "MW"}
    return persist_candidate(
        db_session,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(class_id=CLASS_ID, faculty_id=block_a_teacher, **placement),
                Assignment(class_id=OTHER_CLASS_ID, faculty_id=block_b_teacher, **placement),
            ),
        ),
    )


def test_block_job_result_keeps_a_retired_teacher_pinned_by_the_shared_session(
    db_session: Session,
):
    client = make_api_client(db_session)
    other = _seed_combined_session(db_session)
    # Block B's half of the session is still taught by the retired teacher, so
    # a run for Block A must keep them there.
    _persist_session(db_session, other.faculty_id, FACULTY_ID)
    job = _succeeded_block_job(db_session, FACULTY_ID)
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.commit()

    response = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/preview",
        json={"job_id": str(job.job_id)},
    )

    assert response.status_code == 200, response.text


def test_block_job_result_cannot_reinstate_a_retired_teacher_after_reassignment(
    db_session: Session,
):
    client = make_api_client(db_session)
    other = _seed_combined_session(db_session)
    _persist_session(db_session, FACULTY_ID, FACULTY_ID)
    # Already retired when the run was made, so it carried them only as a pin.
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.flush()
    job = _succeeded_block_job(db_session, FACULTY_ID)
    db_session.commit()
    # Then the whole session was reassigned to someone who still teaches.
    peer = db_session.scalar(
        select(ScheduleRecord).where(ScheduleRecord.class_id == OTHER_CLASS_ID)
    )
    moved = client.patch(
        f"/api/v1/schedules/{SCHEDULE_ID}/assignments/{peer.record_id}",
        json={"faculty_id": str(other.faculty_id)},
    )
    assert moved.status_code == 200, moved.text

    response = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/preview",
        json={"job_id": str(job.job_id)},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "FACULTY_INACTIVE"


def test_a_term_wide_run_never_publishes_a_teacher_deactivated_mid_run(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    db_session.get(Faculty, FACULTY_ID).is_active = False
    db_session.flush()

    with pytest.raises(APIError) as refused:
        persist_candidate(
            db_session,
            CandidateSchedule(
                schedule_id=SCHEDULE_ID,
                term_id=TERM_ID,
                assignments=(
                    Assignment(
                        class_id=CLASS_ID,
                        faculty_id=FACULTY_ID,
                        time_slot_id=TIME_SLOT_ID,
                        room_code="<ROOM-1>",
                        meeting_days="MW",
                    ),
                ),
            ),
        )

    assert refused.value.code == "FACULTY_INACTIVE"
    assert db_session.get(Schedule, SCHEDULE_ID) is None


def test_block_replacement_rejects_finalized_schedule(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    finalized = client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize")
    assert finalized.status_code == 200

    preview_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview",
        json={"assignments": []},
    )
    assert preview_res.status_code == 409
    assert preview_res.json()["code"] == "SCHEDULE_FINALIZED"

    confirm_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/confirm",
        json={"preview_token": "dummy.token"},
    )
    assert confirm_res.status_code == 409
    assert confirm_res.json()["code"] == "SCHEDULE_FINALIZED"


def test_block_replacement_rejects_stale_preview(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)

    preview_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview",
        json={
            "assignments": [
                {
                    "class_id": str(CLASS_ID),
                    "faculty_id": str(FACULTY_ID),
                    "room_code": "<ROOM-1>",
                    "meeting_days": "MW",
                    "time_slot_id": str(TIME_SLOT_ID),
                }
            ]
        },
    )
    assert preview_res.status_code == 200
    token = preview_res.json()["preview_token"]

    # Concurrently edit schedule
    assignment = schedule.records[0]
    client.patch(
        f"/api/v1/schedules/{schedule.schedule_id}/assignments/{assignment.record_id}",
        json={"meeting_days": "TTh"},
    )

    # Confirm with old preview_token should fail with 409 STALE_PREVIEW
    confirm_res = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/confirm",
        json={"preview_token": token},
    )
    assert confirm_res.status_code == 409
    assert confirm_res.json()["code"] == "STALE_PREVIEW"


def test_block_replacement_requires_authentication(db_session: Session):
    client = make_client(db_session)
    preview = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/preview",
        json={"assignments": []},
    )
    confirm = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/confirm",
        json={"preview_token": "dummy"},
    )
    assert preview.status_code == 401
    assert confirm.status_code == 401


def _delete_events(db: Session, outcome: str) -> list[AuditEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.operation == "schedule.delete",
                AuditEvent.outcome == outcome,
            )
        )
    )


def test_delete_candidate_removes_records_and_frees_class_and_offering(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    blocks = f"/api/v1/block-sections/{BLOCK_ID}/classes/{CLASS_ID}"
    offering = f"/api/v1/subject-offerings/{OFFERING_ID}"
    assert client.delete(blocks).status_code == 409
    assert client.delete(offering).status_code == 409

    response = client.delete(f"/api/v1/schedules/{schedule.schedule_id}")

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Schedule, SCHEDULE_ID) is None
    assert not db_session.scalars(
        select(ScheduleRecord).where(ScheduleRecord.schedule_id == SCHEDULE_ID)
    ).all()
    assert len(_delete_events(db_session, "success")) == 1
    assert client.get(f"/api/v1/schedules/{SCHEDULE_ID}").status_code == 404
    assert client.delete(blocks).status_code == 204
    assert client.delete(offering).status_code == 204


def test_delete_refuses_a_finalized_schedule(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200

    response = client.delete(f"/api/v1/schedules/{schedule.schedule_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_FINALIZED"
    db_session.expire_all()
    assert len(db_session.get(Schedule, SCHEDULE_ID).records) == 1
    assert len(_delete_events(db_session, "rejected")) == 1


def test_department_administrator_deletes_a_finalized_schedule(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="admin")
    assert client.post(f"/api/v1/schedules/{schedule.schedule_id}/finalize").status_code == 200

    response = client.delete(f"/api/v1/schedules/{schedule.schedule_id}")

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Schedule, SCHEDULE_ID) is None
    assert len(_delete_events(db_session, "success")) == 1


def test_delete_refuses_a_schedule_holding_historical_records(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    schedule.records[0].is_historical = True
    db_session.commit()
    client = make_api_client(db_session)

    response = client.delete(f"/api/v1/schedules/{schedule.schedule_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_HISTORICAL"
    db_session.expire_all()
    assert db_session.get(Schedule, SCHEDULE_ID) is not None


def test_delete_unknown_schedule_is_404(db_session: Session):
    seed_scheduling_problem(db_session)

    response = make_api_client(db_session).delete(f"/api/v1/schedules/{SCHEDULE_ID}")

    assert response.status_code == 404


def test_preview_token_for_a_deleted_schedule_fails_cleanly(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    preview = client.post(
        f"/api/v1/schedules/{schedule.schedule_id}/blocks/{BLOCK_ID}/preview",
        json={
            "assignments": [
                {
                    "class_id": str(CLASS_ID),
                    "faculty_id": str(FACULTY_ID),
                    "room_code": "<ROOM-1>",
                    "meeting_days": "MW",
                    "time_slot_id": str(TIME_SLOT_ID),
                }
            ]
        },
    ).json()
    assert client.delete(f"/api/v1/schedules/{schedule.schedule_id}").status_code == 204

    response = client.post(
        f"/api/v1/schedules/{SCHEDULE_ID}/blocks/{BLOCK_ID}/confirm",
        json={"preview_token": preview["preview_token"]},
    )

    assert response.status_code == 404


def test_delete_audit_keeps_the_deleted_assignments(db_session: Session):
    schedule = _persist_complete_schedule(db_session)

    make_api_client(db_session).delete(f"/api/v1/schedules/{schedule.schedule_id}")

    [event] = _delete_events(db_session, "success")
    assert event.details["assignments"] == [
        {
            "class_id": str(CLASS_ID),
            "faculty_id": str(FACULTY_ID),
            "room_code": "<ROOM-1>",
            "meeting_days": "MW",
            "time_slot_id": str(TIME_SLOT_ID),
        }
    ]


def test_delete_refuses_a_schedule_whose_job_is_still_active(db_session: Session):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    job = ExecutionJob(
        job_type=JobType.GA.value,
        status=JobStatus.QUEUED.value,
        request={},
        timeout_seconds=60,
        max_attempts=1,
        requested_by=db_session.scalar(select(AppUser.user_id)),
    )
    db_session.add(job)
    db_session.flush()
    schedule.job_id = job.job_id
    db_session.commit()

    response = client.delete(f"/api/v1/schedules/{schedule.schedule_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_JOB_ACTIVE"
    db_session.expire_all()
    assert db_session.get(Schedule, SCHEDULE_ID) is not None
