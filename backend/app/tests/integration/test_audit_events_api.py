"""CHR-183 / CHR-OPS-002 audit trail for protected operations."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppUser, AuditEvent, GAConfiguration, UserRole
from app.tests.integration.test_ga_configuration_api import DEFAULTS
from app.tests.integration.test_historical_uploads_api import URL as UPLOADS
from app.tests.integration.test_historical_uploads_api import TEACHER, _payload, _seed
from app.tests.integration.test_rule_sets_api import (
    _create_rule_set,
    _create_valid_complete_rule_set,
)
from app.tests.integration.test_schedules_api import _persist_complete_schedule
from app.tests.support.api import make_api_client, make_client
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    TERM_ID,
    TIME_SLOT_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

AUDIT = "/api/v1/audit-events"
ADMIN = UserRole.DEPARTMENT_ADMINISTRATOR


def _admin(db: Session):
    return make_api_client(db, role=ADMIN, username="admin")


def _user_id(db: Session, username: str):
    return db.scalar(select(AppUser.user_id).where(AppUser.username == username))


def _events(db: Session, operation: str) -> list[AuditEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.operation == operation)
            .order_by(AuditEvent.occurred_at)
        )
    )


def test_only_administrators_can_read_the_trail(db_session: Session):
    assert make_client(db_session).get(AUDIT).status_code == 401
    scheduler = make_api_client(db_session, role=UserRole.SCHEDULING_COMMITTEE)
    assert scheduler.get(AUDIT).status_code == 403
    assert _admin(db_session).get(AUDIT).status_code == 200


def test_ga_configuration_save_records_before_and_after(db_session: Session):
    client = _admin(db_session)

    saved = client.put("/api/v1/ga-configuration", json={**DEFAULTS, "population_size": 80})

    assert saved.status_code == 200
    [event] = _events(db_session, "ga_configuration.save")
    active = db_session.scalar(select(GAConfiguration).where(GAConfiguration.is_active))
    assert event.outcome == "success"
    assert event.actor_user_id == _user_id(db_session, "admin")
    assert event.target_id == str(active.ga_configuration_id)
    assert event.details["before"]["parameters"]["population_size"] == 50
    assert event.details["after"]["parameters"]["population_size"] == 80

    body = client.get(AUDIT, params={"operation": "ga_configuration.save"}).json()
    [item] = body["items"]
    assert item["actor_username"] == "admin"
    assert item["target_type"] == "ga_configuration"
    assert item["occurred_at"] is not None


def test_rule_set_activation_and_refused_validation_are_both_recorded(
    db_session: Session,
):
    valid = _create_valid_complete_rule_set(db_session, version=1)
    invalid = _create_rule_set(db_session, version=2)  # no terms, streams or rules
    client = _admin(db_session)

    assert client.post(f"/api/v1/rule-sets/{valid.rule_set_id}/activate").status_code == 200
    refused = client.post(f"/api/v1/rule-sets/{invalid.rule_set_id}/activate")

    assert refused.status_code == 409
    success, rejected = _events(db_session, "rule_set.activate")
    assert (success.outcome, success.target_id) == ("success", str(valid.rule_set_id))
    assert success.details["validation"] == "passed"
    assert success.details["after"] == {"status": "active"}
    assert (rejected.outcome, rejected.target_id) == ("rejected", str(invalid.rule_set_id))
    assert rejected.details["error_code"] == "RULE_SET_INVALID"

    rejected_only = client.get(
        AUDIT, params={"operation": "rule_set.activate", "outcome": "rejected"}
    ).json()["items"]
    assert [item["target_id"] for item in rejected_only] == [str(invalid.rule_set_id)]


@pytest.mark.parametrize("export_format", ["csv", "xlsx", "pdf"])
def test_schedule_finalize_and_export_are_attributed_to_the_scheduler(
    db_session: Session, export_format: str,
):
    schedule = _persist_complete_schedule(db_session)
    client = make_api_client(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}"

    assert client.get(f"{base}/export").status_code == 409  # not finalized yet
    assert client.post(f"{base}/finalize").status_code == 200
    assert client.get(f"{base}/export?format={export_format}").status_code == 200

    scheduler_id = _user_id(db_session, "scheduler")
    [finalized] = _events(db_session, "schedule.finalize")
    assert finalized.actor_user_id == scheduler_id
    assert finalized.details["already_finalized"] is False
    refused, exported = _events(db_session, "schedule.export")
    assert refused.outcome == "rejected"
    assert refused.details["error_code"] == "SCHEDULE_NOT_FINALIZED"
    assert exported.outcome == "success"
    assert exported.details["format"] == export_format
    assert exported.details["row_count"] == 1

    admin = _admin(db_session)
    by_target = admin.get(
        AUDIT, params={"target_type": "schedule", "target_id": str(schedule.schedule_id)}
    ).json()["items"]
    assert {item["operation"] for item in by_target} == {
        "schedule.finalize",
        "schedule.export",
    }
    by_actor = admin.get(
        AUDIT, params={"actor_user_id": str(scheduler_id), "outcome": "success"}
    )
    assert len(by_actor.json()["items"]) == 2


def test_schedule_edits_record_before_and_after_without_the_preview_token(
    db_session: Session,
):
    schedule = _persist_complete_schedule(db_session)
    record = schedule.records[0]
    client = make_api_client(db_session)
    base = f"/api/v1/schedules/{schedule.schedule_id}"

    edited = client.patch(
        f"{base}/assignments/{record.record_id}", json={"meeting_days": "TTh"}
    )
    preview = client.post(
        f"{base}/blocks/{BLOCK_ID}/preview",
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
    token = preview["preview_token"]
    replaced = client.post(
        f"{base}/blocks/{BLOCK_ID}/confirm", json={"preview_token": token}
    )
    stale = client.post(f"{base}/blocks/{BLOCK_ID}/confirm", json={"preview_token": token})

    assert (edited.status_code, replaced.status_code, stale.status_code) == (200, 200, 409)
    [edit] = _events(db_session, "schedule.assignment_update")
    [change] = edit.details["changes"]
    assert change["record_id"] == str(record.record_id)
    assert (change["before"]["meeting_days"], change["after"]["meeting_days"]) == ("MW", "TTh")
    success, refused = _events(db_session, "schedule.block_replace")
    assert success.details["before"][0]["meeting_days"] == "TTh"
    assert success.details["after"][0]["meeting_days"] == "MW"
    assert (refused.outcome, refused.details["error_code"]) == ("rejected", "STALE_PREVIEW")
    assert token not in json.dumps([success.details, refused.details])


def test_historical_import_events_carry_no_raw_historical_data(
    db_session: Session, tmp_path: Path
):
    _seed(db_session)
    client = _admin(db_session)
    staged = client.post(UPLOADS, json=_payload()).json()
    confirmed = client.post(
        f"{UPLOADS}/{staged['batch_id']}/confirm",
        json={"content_sha256": staged["content_sha256"]},
    )

    assert confirmed.status_code == 200
    [stage] = _events(db_session, "historical_upload.stage")
    [confirm] = _events(db_session, "historical_upload.confirm")
    assert stage.target_id == confirm.target_id == staged["batch_id"]
    assert stage.details["content_sha256"] == staged["content_sha256"]
    assert stage.details["accepted_count"] == 2
    assert confirm.details["after"] == {"status": "committed"}
    assert len(confirm.details["snapshot_ids"]) == 2
    serialized = json.dumps([stage.details, confirm.details])
    assert TEACHER not in serialized
    assert "canonical_records" not in serialized
    assert "DELA CRUZ" not in serialized.upper()


def test_refused_upload_records_only_hash_and_sizes(db_session: Session, tmp_path: Path):
    _seed(db_session)
    client = _admin(db_session)
    payload = _payload()
    assert client.post(UPLOADS, json=payload).status_code == 201

    duplicate = client.post(UPLOADS, json=payload)

    assert duplicate.status_code == 409
    _staged, refused = _events(db_session, "historical_upload.stage")
    assert refused.outcome == "rejected"
    assert refused.details["error_code"] == "HISTORY_UPLOAD_DUPLICATE"
    assert isinstance(refused.details["file_bytes"], int)
    assert len(refused.details["source_workbook_sha256"]) == 64
    assert TEACHER not in json.dumps(refused.details)


def test_audit_events_are_append_only(db_session: Session):
    client = _admin(db_session)
    assert client.put("/api/v1/ga-configuration", json=DEFAULTS).status_code == 200

    for statement in ("UPDATE audit_event SET outcome = 'rejected'", "DELETE FROM audit_event"):
        with pytest.raises(IntegrityError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text(statement))
    assert len(_events(db_session, "ga_configuration.save")) == 1


def test_audit_list_rejects_an_unknown_outcome_filter(db_session: Session):
    response = _admin(db_session).get(AUDIT, params={"outcome": "maybe"})
    assert response.status_code == 422


def test_unparsable_upload_rejection_points_at_the_kept_batch(
    db_session: Session, tmp_path: Path
):
    _seed(db_session)
    client = _admin(db_session)
    refused = client.post(UPLOADS, json=_payload(b"not an xlsx workbook"))

    assert refused.status_code == 422
    [event] = _events(db_session, "historical_upload.stage")
    assert event.outcome == "rejected"
    assert event.details["error_details"] == {
        "batch_id": refused.json()["details"]["batch_id"]
    }
    assert TEACHER not in json.dumps(event.details)


def test_target_filter_ignores_uuid_case_and_master_events_share_a_target_type(
    db_session: Session,
):
    seed_scheduling_problem(db_session)
    db_session.commit()
    client = _admin(db_session)
    created = client.post("/api/v1/schedules/master", json={"term_id": str(TERM_ID)})
    duplicate = client.post("/api/v1/schedules/master", json={"term_id": str(TERM_ID)})

    assert (created.status_code, duplicate.status_code) == (201, 409)
    success, refused = _events(db_session, "schedule.master_create")
    assert success.target_type == refused.target_type == "schedule"
    assert refused.details["term_id"] == str(TERM_ID)
    schedule_id = created.json()["schedule_id"]
    items = client.get(
        AUDIT, params={"target_type": "schedule", "target_id": schedule_id.upper()}
    ).json()["items"]
    assert [item["outcome"] for item in items] == ["success"]


@pytest.mark.parametrize("param", ["operation", "target_type", "target_id"])
def test_audit_list_rejects_nul_in_string_filters(db_session: Session, param: str):
    client = _admin(db_session)

    assert client.get(AUDIT, params={param: "bad\x00filter"}).status_code == 422
    assert client.get(AUDIT, params={param: "fine"}).status_code == 200


@pytest.mark.parametrize("param", ["occurred_from", "occurred_to"])
@pytest.mark.parametrize(
    ("value", "status"),
    [
        ("2020-01-01T00:00:00+15:59", 200),
        ("2020-01-01T00:00:00+16:00", 200),
        ("2020-01-01T00:00:00-18:00", 200),
        ("0001-01-01T00:00:00+23:59", 422),
        ("9999-12-31T23:59:59-23:59", 422),
    ],
)
def test_audit_list_handles_out_of_range_offsets(
    db_session: Session, param: str, value: str, status: int
):
    client = _admin(db_session)

    assert client.get(AUDIT, params={param: value}).status_code == status


def test_naive_audit_bounds_are_read_as_utc_whatever_the_session_timezone(
    db_session: Session,
):
    db_session.add(
        AuditEvent(
            operation="test.naive_bound",
            target_type="test",
            outcome="success",
            occurred_at=datetime(2020, 1, 1, 12, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    client = _admin(db_session)
    db_session.execute(text("SET TIME ZONE 'America/Los_Angeles'"))
    try:
        for suffix in ("", "Z"):
            items = client.get(
                AUDIT,
                params={
                    "operation": "test.naive_bound",
                    "occurred_to": f"2020-01-01T11:00:00{suffix}",
                },
            ).json()["items"]
            assert items == []
    finally:
        db_session.execute(text("RESET TIME ZONE"))


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00", "9999-12-31T23:59:59"])
def test_naive_extreme_audit_bounds_do_not_fail(db_session: Session, value: str):
    client = _admin(db_session)

    assert client.get(AUDIT, params={"occurred_from": value}).status_code == 200


def test_invalid_base64_upload_records_a_rejected_event(
    db_session: Session, tmp_path: Path
):
    _seed(db_session)
    client = _admin(db_session)
    payload = _payload()
    payload["workbook"] = "not base64!"

    refused = client.post(UPLOADS, json=payload)

    assert refused.status_code == 422
    [event] = _events(db_session, "historical_upload.stage")
    assert event.outcome == "rejected"
    assert event.details["error_code"] == "HISTORY_UPLOAD_INVALID"


def test_rejected_stage_event_is_found_by_its_batch_id(
    db_session: Session, tmp_path: Path
):
    _seed(db_session)
    client = _admin(db_session)
    refused = client.post(UPLOADS, json=_payload(b"not an xlsx workbook"))

    batch_id = refused.json()["details"]["batch_id"]
    items = client.get(
        AUDIT, params={"target_type": "historical_import_batch", "target_id": batch_id}
    ).json()["items"]
    assert [item["outcome"] for item in items] == ["rejected"]
