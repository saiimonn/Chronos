"""CHR-193 / CHR-FIX-022: delete refusals name the records that block them."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Faculty, Room, ScheduleRecord, SubjectOffering
from app.services.catalog.guards import count_where, ensure_not_referenced
from app.tests.integration.test_schedule import make_assignment, make_problem_data
from app.tests.support.api import make_api_client

pytestmark = pytest.mark.postgres

LABEL = "CS300 · BSCS-1A · 2025-2026 T1 (routine candidate)"


def _schedule_record_refusal(db: Session, faculty_id) -> dict:
    """The refusal a guarded delete would give for this member's records.

    Faculty are no longer deleted (DELETE deactivates), so the labels are
    exercised through the guard directly.
    """
    with pytest.raises(APIError) as caught:
        ensure_not_referenced(
            db,
            [(
                "schedule_records",
                count_where(ScheduleRecord, ScheduleRecord.faculty_id == faculty_id),
            )],
        )
    assert caught.value.status_code == 409
    return caught.value.details


def test_faculty_delete_deactivates_and_keeps_assignments(db_session: Session):
    data = make_problem_data(db_session)
    db_session.add(make_assignment(data))
    db_session.flush()
    client = make_api_client(db_session)

    response = client.delete(f"/api/v1/faculty/{data[3].faculty_id}")

    assert response.status_code == 204
    member = db_session.get(Faculty, data[3].faculty_id)
    assert member is not None and member.is_active is False
    assert db_session.query(ScheduleRecord).filter_by(
        faculty_id=data[3].faculty_id
    ).count() == 1


def test_guard_names_the_blocking_assignments(db_session: Session):
    data = make_problem_data(db_session)
    db_session.add(make_assignment(data))
    db_session.flush()

    details = _schedule_record_refusal(db_session, data[3].faculty_id)
    assert details["dependents"] == {"schedule_records": 1}
    assert details["dependent_records"] == {
        "schedule_records": {"total": 1, "items": [LABEL]}
    }


def test_room_delete_deactivates_and_keeps_assignments(db_session: Session):
    data = make_problem_data(db_session)
    db_session.add(
        Room(room_code="LB204", building_name="Lawton", capacity=20,
             room_type="laboratory")
    )
    db_session.flush()
    db_session.add(make_assignment(data, room_code="LB204"))
    db_session.flush()
    client = make_api_client(db_session)

    response = client.delete("/api/v1/rooms/LB204")

    assert response.status_code == 204
    room = db_session.get(Room, "LB204")
    assert room is not None and room.is_active is False
    assert db_session.query(ScheduleRecord).filter_by(room_code="LB204").count() == 1


def test_labels_are_capped_but_total_counts_every_record(db_session: Session):
    data = make_problem_data(db_session)
    db_session.add_all(
        make_assignment(data, schedule_id=None, class_id=None, time_slot_id=None)
        for _ in range(12)
    )
    db_session.flush()

    details = _schedule_record_refusal(db_session, data[3].faculty_id)
    group = details["dependent_records"]["schedule_records"]
    assert group["total"] == 12
    assert len(group["items"]) == 10
    assert group["items"][0] == "CS300 · 2025-2026 T1 (unplaced)"


def test_finalized_schedule_is_labelled_finalized(db_session: Session):
    data = make_problem_data(db_session)
    data[6].finalized_at = datetime.now(timezone.utc)
    db_session.add(make_assignment(data))
    db_session.flush()

    details = _schedule_record_refusal(db_session, data[3].faculty_id)
    items = details["dependent_records"]["schedule_records"]["items"]
    assert items == ["CS300 · BSCS-1A · 2025-2026 T1 (routine finalized)"]


def test_term_delete_names_the_blocking_blocks(db_session: Session):
    data = make_problem_data(db_session)
    # Terms are archived through the API, never deleted, so the guarded
    # service delete is what still names a term's dependents.
    from app.services.catalog import terms

    with pytest.raises(APIError) as refused:
        terms.delete_term(db_session, data[0].term_id)

    assert refused.value.status_code == 409
    records = refused.value.details["dependent_records"]
    assert records["block_sections"] == {"total": 1, "items": ["BSCS-1A · 2025-2026 T1"]}


def test_offering_delete_names_the_blocks_that_take_it(db_session: Session):
    data = make_problem_data(db_session)
    offering_id = data[4].offering_id
    client = make_api_client(db_session)

    refused = client.delete(f"/api/v1/subject-offerings/{offering_id}")

    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "DEPENDENT_RECORDS_EXIST"
    assert body["details"]["dependents"] == {"block_classes": 1}
    assert body["details"]["dependent_records"] == {
        "block_classes": {"total": 1, "items": ["CS300 · BSCS-1A · 2025-2026 T1"]}
    }
    assert db_session.get(SubjectOffering, offering_id) is not None


def test_unreferenced_offering_is_deleted(db_session: Session):
    data = make_problem_data(db_session)
    spare = SubjectOffering(
        term_id=data[0].term_id, subject_code=data[1].subject_code, group_no=2
    )
    db_session.add(spare)
    db_session.flush()
    client = make_api_client(db_session)

    deleted = client.delete(f"/api/v1/subject-offerings/{spare.offering_id}")

    assert deleted.status_code == 204
