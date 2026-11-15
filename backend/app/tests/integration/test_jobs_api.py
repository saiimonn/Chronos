"""CHR-31 / CHR-WORKER-001 job API acceptance tests."""

import json
from datetime import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    BlockSection,
    BlockSectionOffering,
    ExecutionJob,
    Faculty,
    JobStatus,
    JobType,
    Schedule,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
    UserRole,
)
from app.models.historical_snapshot import HistoricalRecord, HistoricalSnapshot
from app.models.rule_set import RuleSetThreshold
from app.repositories.jobs import JobRepository
from app.repositories.scheduling import SQLAlchemyBlockGenerationContextProvider
from app.services.job_submission import UNUSABLE_SELECTION_MESSAGE
from app.services.jobs.codecs import decode_mining_thresholds
from app.services.jobs.handlers import run_arm
from app.services.readiness import analyze_readiness
from app.tests.support.api import make_api_client, make_client
from app.tests.support.history import add_to_curriculum, seed_historical_terms
from app.tests.support.scheduling import (
    BLOCK_ID,
    FACULTY_ID,
    OFFERING_ID,
    SCHEDULE_ID,
    TERM_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

ARM_THRESHOLDS = {
    stream: {
        "min_support": "0.1",
        "min_confidence": "0.5",
        "min_lift": "1.0",
        "min_term_recurrence": 1,
    }
    for stream in (
        "teacher_subject",
        "teacher_day",
        "teacher_time",
        "teacher_subject_room",
    )
}

# Authorization is refused before the selection is read, so tests that expect
# 401/403 need no real history.
ARM_REQUEST = {
    "selected_snapshot_ids": ["00000000-0000-0000-0000-000000000001"],
    "thresholds": ARM_THRESHOLDS,
    "notes": "API contract test",
}


def _arm_request(db_session: Session) -> dict:
    """An ARM request over history that actually exists and can be trained on."""
    return {
        "selected_snapshot_ids": [
            str(snapshot_id) for snapshot_id in seed_historical_terms(db_session)
        ],
        "thresholds": ARM_THRESHOLDS,
        "notes": "API contract test",
    }


def _create_term(client) -> str:
    response = client.post(
        "/api/v1/terms", json={"academic_year": "2025-2026", "term_number": 1}
    )
    assert response.status_code == 201
    return response.json()["term_id"]


def _ready_term(db_session: Session) -> str:
    seed_scheduling_problem(db_session)
    return str(TERM_ID)


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("per_block", [False, True])
def test_ga_submission_refuses_an_unready_term_without_enqueuing(
    db_session: Session, role: UserRole, per_block: bool,
):
    """CHR-124 / CHR-BLK-007 enforces readiness for both roles and scopes."""
    seed_scheduling_problem(db_session)
    db_session.get(Faculty, FACULTY_ID).availability_mask = "0"
    db_session.add(Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID))
    db_session.flush()
    client = make_api_client(db_session, role=role)
    report = client.get(f"/api/v1/terms/{TERM_ID}/readiness").json()
    request = {"term_id": str(TERM_ID)}
    if per_block:
        request.update(block_id=str(BLOCK_ID), base_schedule_id=str(SCHEDULE_ID))

    response = client.post("/api/v1/jobs/ga", json=request)

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_NOT_READY"
    assert response.json()["details"] == {
        "ready": False, "blockers": report["blockers"],
    }
    assert "location" not in response.headers
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 0


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("per_block", [False, True])
def test_ga_submission_accepts_unreviewed_availability(
    db_session: Session, role: UserRole, per_block: bool,
):
    """CHR-235: availability review is optional, so it never gates submission."""
    seed_scheduling_problem(db_session)
    db_session.get(Faculty, FACULTY_ID).availability_reviewed_term_id = None
    db_session.add(Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID))
    db_session.flush()
    client = make_api_client(db_session, role=role)
    report = client.get(f"/api/v1/terms/{TERM_ID}/readiness").json()
    assert report["ready"] is True
    request = {"term_id": str(TERM_ID)}
    if per_block:
        request.update(block_id=str(BLOCK_ID), base_schedule_id=str(SCHEDULE_ID))

    response = client.post("/api/v1/jobs/ga", json=request)

    assert response.status_code == 202, response.text
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 1


@pytest.mark.parametrize("role", list(UserRole))
def test_block_submission_checks_the_entire_terms_structural_readiness(
    db_session: Session, role: UserRole,
):
    from app.tests.integration.test_block_generation_context_repository import (
        SECOND_SLOT_ID,
        seed_base_schedule,
        seed_second_block,
    )

    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    db_session.get(TimeSlot, SECOND_SLOT_ID).end_time = time(12)
    db_session.get(Faculty, FACULTY_ID).availability_mask = "11"
    seed_base_schedule(db_session)
    context = SQLAlchemyBlockGenerationContextProvider(db_session).get_block_generation_context(
        TERM_ID, BLOCK_ID, SCHEDULE_ID
    )
    assert analyze_readiness(context.selected_problem).ready
    client = make_api_client(db_session, role=role)

    response = client.post("/api/v1/jobs/ga", json={
        "term_id": str(TERM_ID), "block_id": str(BLOCK_ID),
        "base_schedule_id": str(SCHEDULE_ID),
    })

    assert response.status_code == 409
    assert [item["code"] for item in response.json()["details"]["blockers"]] == [
        "LAB_SUBJECT_WITHOUT_LAB_ROOM"
    ]
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 0


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("per_block", [False, True])
def test_shared_offering_demand_and_curriculum_warnings_do_not_block_submission(
    db_session: Session, role: UserRole, per_block: bool,
):
    """CHR-124 / CHR-BLK-007: four shared 3-unit Offerings need 12, not 24."""
    seed_scheduling_problem(db_session)
    peer_block_id = UUID(int=124000)
    db_session.add(BlockSection(
        block_id=peer_block_id, term_id=TERM_ID, program_code="<PROGRAM-1>",
        year_level=1, section_letter="B", is_expanded=False,
    ))
    db_session.flush()
    for index in range(4):
        offering_id = OFFERING_ID
        subject_code = "<SUBJECT-1>"
        if index:
            offering_id = UUID(int=124010 + index)
            subject_code = f"<SUBJECT-{index + 1}>"
            db_session.add(Subject(
                subject_code=subject_code, subject_title=f"<SUBJECT-TITLE-{index + 1}>",
                units=3, subject_type="LEC",
            ))
            db_session.flush()
            db_session.add(SubjectOffering(
                offering_id=offering_id, term_id=TERM_ID,
                subject_code=subject_code, group_no=1,
            ))
            db_session.flush()
            db_session.add(BlockSectionOffering(
                class_id=UUID(int=124100 + 2 * index), block_id=BLOCK_ID,
                offering_id=offering_id, term_id=TERM_ID, subject_code=subject_code,
            ))
        db_session.add(BlockSectionOffering(
            class_id=UUID(int=124101 + 2 * index), block_id=peer_block_id,
            offering_id=offering_id, term_id=TERM_ID, subject_code=subject_code,
        ))
    db_session.add(Schedule(schedule_id=SCHEDULE_ID, term_id=TERM_ID))
    db_session.flush()
    client = make_api_client(db_session, role=role)
    report = client.get(f"/api/v1/terms/{TERM_ID}/readiness").json()
    assert report["ready"] and report["blockers"] == []
    assert report["warnings"]
    request = {"term_id": str(TERM_ID)}
    if per_block:
        request.update(block_id=str(BLOCK_ID), base_schedule_id=str(SCHEDULE_ID))

    response = client.post("/api/v1/jobs/ga", json=request)

    assert response.status_code == 202, response.text
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 1


def test_committee_can_enqueue_ga_but_not_arm(db_session: Session):
    client = make_api_client(db_session)
    term_id = _ready_term(db_session)

    ga = client.post("/api/v1/jobs/ga", json={"term_id": term_id})
    arm = client.post("/api/v1/jobs/arm", json=ARM_REQUEST)

    assert ga.status_code == 202
    assert arm.status_code == 403


def test_administrator_can_enqueue_ga_and_arm(db_session: Session):
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    term_id = _ready_term(db_session)
    # ARM mines only subjects a curriculum governing the active term's year
    # still requires, so the active term needs a real academic year.
    db_session.get(Term, TERM_ID).academic_year = "2025-2026"
    db_session.flush()

    assert client.post("/api/v1/jobs/ga", json={"term_id": term_id}).status_code == 202
    arm = client.post("/api/v1/jobs/arm", json=_arm_request(db_session))
    assert arm.status_code == 202
    assert client.get(arm.headers["location"]).status_code == 200


def test_arm_submission_records_the_selected_history(db_session: Session):
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)

    accepted = client.post("/api/v1/jobs/arm", json=request)

    assert accepted.status_code == 202
    persisted = JobRepository(db_session).get(UUID(accepted.json()["job_id"]))
    assert persisted is not None
    assert (
        persisted.request["selected_snapshot_ids"] == request["selected_snapshot_ids"]
    )
    assert {term["snapshot_id"] for term in persisted.request["terms"]} == set(
        request["selected_snapshot_ids"]
    )
    # The worker stamps these on the draft it publishes (CHR-ARM-008).
    assert persisted.request["job_id"] == str(persisted.job_id)
    assert persisted.request["requested_by"] == str(persisted.requested_by)


@pytest.mark.parametrize("raw_number", [False, True], ids=["string", "number"])
@pytest.mark.parametrize("stream", list(ARM_THRESHOLDS))
@pytest.mark.parametrize("field,value", [
    ("min_support", "0.00001"),
    ("min_confidence", "0.123456"),
    ("min_lift", "0.123456"),
    ("min_lift", "10000"),
    ("min_support", "0.12340000000000000000000000000000001"),
    ("min_confidence", "0.12340000000000000000000000000000001"),
    ("min_lift", "0.12340000000000000000000000000000001"),
    ("min_lift", "9999.99990000000000000000000000000001"),
    ("min_support", "1.00000000000000001"),
    ("min_confidence", "1.00000000000000001"),
    ("min_lift", "9999.999900000000001"),
])
def test_arm_submission_rejects_inexact_thresholds_before_enqueue(
    db_session: Session, stream, field, value, raw_number,
):
    """CHR-171 / CHR-FIX-011: refuse rounding and overflow in every stream."""
    client = make_api_client(db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR)
    request = _arm_request(db_session)
    request["thresholds"] = {
        name: {**thresholds, **({field: value} if name == stream else {})}
        for name, thresholds in ARM_THRESHOLDS.items()
    }

    content = json.dumps(request)
    if raw_number:
        content = content.replace(f'"{field}": "{value}"', f'"{field}": {value}')
    response = client.post(
        "/api/v1/jobs/arm", content=content, headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["field"] == f"body.thresholds.{stream}.{field}"
    assert all(set(error) == {"field", "type"} for error in body["details"]["errors"])
    assert "location" not in response.headers
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 0


@pytest.mark.parametrize("raw_numbers", [False, True], ids=["strings", "numbers"])
@pytest.mark.parametrize("zero", ["0", "0e2147483648", "0e-2147483648"])
def test_arm_thresholds_round_trip_from_request_through_worker_to_postgres(
    db_session: Session, monkeypatch, zero, raw_numbers,
):
    """CHR-171 / CHR-FIX-011: all three decimals survive real publication."""
    client = make_api_client(db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR)
    request = _arm_request(db_session)
    request["thresholds"] = {
        "teacher_subject": dict(
            min_support="0.0001", min_confidence="0.1234", min_lift="0", min_term_recurrence=1,
        ),
        "teacher_day": dict(
            min_support="1e-4", min_confidence="1.00000",
            min_lift="0.12340000000000000000000000000000000", min_term_recurrence=1,
        ),
        "teacher_time": dict(
            min_support=0.1234, min_confidence="0.000100", min_lift=zero, min_term_recurrence=1,
        ),
        "teacher_subject_room": dict(
            min_support="1", min_confidence="1", min_lift="9999.9999", min_term_recurrence=1,
        ),
    }

    content = json.dumps(request)
    if raw_numbers:
        for thresholds in request["thresholds"].values():
            for field in ("min_support", "min_confidence", "min_lift"):
                value = thresholds[field]
                content = content.replace(json.dumps(value), str(value))
    response = client.post(
        "/api/v1/jobs/arm", content=content, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    repository = JobRepository(db_session)
    job = repository.get(job_id)
    payload = job.request
    decoded = decode_mining_thresholds(payload["thresholds"])
    claim = repository.claim_next(
        claimed_by="exact-threshold-test", lease_seconds=300, job_types=[JobType.ARM],
    )
    assert claim is not None and claim.job_id == job_id
    monkeypatch.setattr(
        "app.db.session.SessionLocal",
        sessionmaker(bind=db_session.connection(), join_transaction_mode="create_savepoint"),
    )

    result = run_arm({**payload, "claim_token": str(claim.claim_token)})

    assert payload["thresholds"]["teacher_time"]["min_lift"] == "0"
    stored = db_session.scalars(select(RuleSetThreshold).where(
        RuleSetThreshold.rule_set_id == UUID(result["rule_set_id"]),
    )).all()
    assert {row.pattern_type for row in stored} == set(ARM_THRESHOLDS)
    for row in stored:
        for field in ("min_support", "min_confidence", "min_lift", "min_term_recurrence"):
            expected = Decimal(str(request["thresholds"][row.pattern_type][field]))
            assert Decimal(str(payload["thresholds"][row.pattern_type][field])) == expected
            assert getattr(decoded[row.pattern_type], field) == expected
            assert getattr(row, field) == expected


def test_arm_request_openapi_keeps_the_typed_json_body(db_session: Session):
    client = make_client(db_session)
    schema = client.get("/openapi.json").json()

    assert schema["paths"]["/api/v1/jobs/arm"]["post"]["requestBody"] == {
        "content": {"application/json": {"schema": {
            "$ref": "#/components/schemas/SubmitARMJobRequest",
        }}},
        "required": True,
    }
    fields = schema["components"]["schemas"]["ARMStreamThresholdsRequest"]["properties"]
    for field in ("min_support", "min_confidence", "min_lift"):
        assert {option["type"] for option in fields[field]["anyOf"]} == {"number", "string"}


@pytest.mark.parametrize("content,content_type", [
    ("", "application/json"),
    ("{", "application/json"),
    ("[]", "application/json"),
    ("null", "application/json"),
    (json.dumps(ARM_REQUEST), "text/plain"),
    (
        json.dumps(ARM_REQUEST).replace('"min_lift": "1.0"', '"min_lift": 0e999999999999999999999'),
        "application/json",
    ),
])
def test_arm_request_keeps_standard_json_validation(db_session: Session, content, content_type):
    client = make_api_client(db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR)

    response = client.post(
        "/api/v1/jobs/arm", content=content, headers={"Content-Type": content_type},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"]
    assert all(set(error) == {"field", "type"} for error in body["details"]["errors"])
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 0


def test_arm_submission_rejects_a_snapshot_that_does_not_exist(db_session: Session):
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)
    unknown = "00000000-0000-0000-0000-0000000000ff"
    request["selected_snapshot_ids"].append(unknown)

    response = client.post("/api/v1/jobs/arm", json=request)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["selected_snapshot_ids"] == [unknown]


def test_arm_submission_accepts_a_selection_no_larger_than_the_holdout(
    db_session: Session,
):
    """Operational mining withholds no holdout (thesis §4.3), so three terms that
    evaluation would reserve entirely can still be mined."""
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)
    request["selected_snapshot_ids"] = request["selected_snapshot_ids"][:3]

    response = client.post("/api/v1/jobs/arm", json=request)

    assert response.status_code == 202


def test_arm_submission_rejects_training_terms_without_records(db_session: Session):
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    snapshots = [
        HistoricalSnapshot(
            source_term_id=uuid4(),
            academic_year=f"{2020 + index}-{2021 + index}",
            term_number=1,
        )
        for index in range(4)
    ]
    db_session.add_all(snapshots)
    db_session.flush()

    response = client.post(
        "/api/v1/jobs/arm",
        json={
            "selected_snapshot_ids": [
                str(snapshot.snapshot_id) for snapshot in snapshots
            ],
            "thresholds": ARM_THRESHOLDS,
            "notes": "CHR-74 / CHR-RBAC-005 empty-history guard",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "no mineable historical records" in response.json()["message"]


def test_arm_submission_rejects_a_malformed_academic_year(db_session: Session):
    """A stored year the inclusion policy cannot read is listed as eligible for
    no stream, so it can still be selected. Preprocessing raises a bare
    ValueError on it, which must be an answer about the request rather than a
    server error."""
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)
    # Captured history is immutable once written, so the malformed year is
    # introduced the only way it can occur: at capture time.
    snapshot = HistoricalSnapshot(
        source_term_id=uuid4(),
        academic_year="2024/2025",
        term_number=9,
    )
    db_session.add(snapshot)
    db_session.flush()
    malformed = str(snapshot.snapshot_id)
    request["selected_snapshot_ids"].append(malformed)

    response = client.post("/api/v1/jobs/arm", json=request)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["selected_snapshot_ids"] == [malformed]


def test_arm_submission_reports_a_preprocessing_refusal_without_internal_detail(
    db_session: Session,
):
    """Reading the selection validates it, signalling a refusal with a bare
    ValueError.

    A captured room code left empty is one such case the database still permits.
    It is an answer about the selection, so it is reported as a 422 — but with
    the service's fixed public message, so no internal text is echoed back and no
    unrelated defect can be misreported as client input.
    """
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)
    selected = UUID(request["selected_snapshot_ids"][0])
    db_session.add(
        Subject(
            subject_code="ARM102",
            subject_title="<ARM SUBJECT>",
            units=3,
            subject_type="LEC",
        )
    )
    db_session.flush()
    add_to_curriculum(db_session, "ARM102")
    # History is append-only and its rows can never be corrected, so the empty
    # room code is captured rather than edited in — which is the only way it
    # occurs, and the reason the reader has to answer for it.
    db_session.add(
        HistoricalRecord(
            snapshot_id=selected,
            source_faculty_id=uuid4(),
            faculty_name="<ARM FACULTY>",
            subject_code="ARM102",
            subject_title="<ARM SUBJECT>",
            room_code="",
            meeting_days="TTH",
            time_slot="09:00-10:30",
        )
    )
    db_session.flush()

    response = client.post("/api/v1/jobs/arm", json=request)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == UNUSABLE_SELECTION_MESSAGE
    assert "room_code" not in body["message"]


def test_arm_submission_rejects_notes_longer_than_the_column_stores(
    db_session: Session,
):
    """`RuleSet.notes` is VARCHAR(500); a longer note would be accepted here and
    then fail when the worker persists the draft."""
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)

    assert (
        client.post("/api/v1/jobs/arm", json={**request, "notes": "n" * 500}).status_code
        == 202
    )
    assert (
        client.post("/api/v1/jobs/arm", json={**request, "notes": "n" * 501}).status_code
        == 422
    )


def test_arm_submission_rejects_duplicate_and_empty_selections(db_session: Session):
    client = make_api_client(
        db_session,
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
        username="administrator",
    )
    request = _arm_request(db_session)
    duplicated = dict(request)
    duplicated["selected_snapshot_ids"] = [request["selected_snapshot_ids"][0]] * 2

    assert client.post("/api/v1/jobs/arm", json=duplicated).status_code == 422
    assert (
        client.post(
            "/api/v1/jobs/arm", json={**request, "selected_snapshot_ids": []}
        ).status_code
        == 422
    )


def test_job_endpoints_require_authentication(db_session: Session):
    client = make_client(db_session)

    assert (
        client.post(
            "/api/v1/jobs/ga",
            json={"term_id": "00000000-0000-0000-0000-000000000001"},
        ).status_code
        == 401
    )
    assert client.post("/api/v1/jobs/arm", json=ARM_REQUEST).status_code == 401
    assert (
        client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000001").status_code
        == 401
    )


def test_enqueue_is_durable_and_polling_exposes_only_safe_state(db_session: Session):
    client = make_api_client(db_session)
    term_id = _ready_term(db_session)

    accepted = client.post(
        "/api/v1/jobs/ga",
        json={"term_id": term_id},
    )

    assert accepted.status_code == 202
    body = accepted.json()
    assert body["kind"] == "ga"
    assert body["status"] == "queued"
    assert accepted.headers["location"] == f"/api/v1/jobs/{body['job_id']}"

    persisted = JobRepository(db_session).get(UUID(body["job_id"]))
    assert persisted is not None
    assert persisted.status is JobStatus.QUEUED
    assert persisted.request["problem"]["term"]["term_id"] == term_id

    polled = client.get(accepted.headers["location"])
    assert polled.status_code == 200
    assert polled.json()["job_id"] == body["job_id"]
    assert polled.json()["result"] is None
    assert polled.json()["error"] is None
    assert {
        "request",
        "claim_token",
        "claimed_by",
        "lease_expires_at",
        "timeout_seconds",
        "max_attempts",
    }.isdisjoint(polled.json())


def test_ga_polling_reports_the_rule_set_frozen_at_submission(db_session: Session):
    client = make_api_client(db_session)
    term_id = _ready_term(db_session)
    accepted = client.post("/api/v1/jobs/ga", json={"term_id": term_id})
    location = accepted.headers["location"]
    job_id = UUID(accepted.json()["job_id"])
    request = db_session.get(ExecutionJob, job_id).request

    def store(payload: dict) -> None:
        db_session.execute(
            update(ExecutionJob).where(ExecutionJob.job_id == job_id).values(request=payload)
        )
        db_session.expire_all()

    # No active rule set at submission: the run is unguided.
    assert client.get(location).json()["guidance"] == {
        "mode": "unguided", "rule_set_id": None, "rule_set_version": None,
    }

    from app.services.arm.preprocessing import PatternStream as Stream
    from app.services.arm.providers import GuidanceRule, HistoricalGuidance
    from app.services.jobs.codecs import encode_guidance_snapshot

    rule_set_id = uuid4()
    guided = encode_guidance_snapshot(
        HistoricalGuidance(
            (
                GuidanceRule(
                    stream=Stream.TEACHER_SUBJECT,
                    faculty_id=uuid4(),
                    subject_code="CS101",
                    room_code=None,
                    meeting_days=None,
                    time_slot=None,
                    guidance_score=Decimal("0.5"),
                    support=Decimal("0.2"),
                    confidence=Decimal("0.7"),
                    lift=Decimal("1.2"),
                    term_recurrence=2,
                ),
            ),
            rule_set_id=rule_set_id,
            version=3,
        )
    )
    store({**request, "historical_guidance": guided})
    polled = client.get(location).json()
    assert polled["guidance"] == {
        "mode": "guided", "rule_set_id": str(rule_set_id), "rule_set_version": 3,
    }
    # Only the identity is exposed, never the frozen rules themselves.
    assert "rules" not in polled["guidance"]

    # Whatever the worker would refuse is reported unknown: polling keeps
    # working, and a run is never claimed unguided on a guess.
    missing = object()
    for snapshot in (
        missing,
        "guided",
        {**guided, "mode": "partial"},
        {**guided, "mode": "unguided", "rules": []},
        {**guided, "rule_set_id": "not-a-uuid"},
        {**guided, "rule_set_id": "00000000-0000-0000-0000-000000000000"},
        {**guided, "rule_set_id": None},
        {**guided, "rule_set_version": "3"},
        {**guided, "rule_set_version": 3.0},
        {**guided, "rule_set_version": True},
        {**guided, "rule_set_version": 0},
        {**guided, "rule_set_version": -2},
        {**guided, "rule_set_version": None},
        {**guided, "rules": []},
    ):
        payload = {key: value for key, value in request.items() if key != "historical_guidance"}
        if snapshot is not missing:
            payload["historical_guidance"] = snapshot
        store(payload)
        corrupt = client.get(location)
        assert corrupt.status_code == 200, snapshot
        assert corrupt.json()["guidance"] is None, snapshot

    # Payloads queued before guidance snapshots existed (codec < 7) ran unguided.
    store({
        **{key: value for key, value in request.items() if key != "historical_guidance"},
        "version": 6,
    })
    assert client.get(location).json()["guidance"]["mode"] == "unguided"


def test_enqueue_does_not_run_ga_in_the_api_process(db_session: Session, monkeypatch):
    client = make_api_client(db_session)
    term_id = _ready_term(db_session)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("GA ran synchronously in the API process")

    monkeypatch.setattr("app.services.ga.engine.GAEngine.run", fail_if_called)

    response = client.post("/api/v1/jobs/ga", json={"term_id": term_id})

    assert response.status_code == 202


def test_submit_ga_with_block_requires_base_schedule(db_session: Session):
    client = make_api_client(db_session)
    term_id = _create_term(client)
    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": term_id,
            "block_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 422


def test_submit_ga_block_not_found_returns_404(db_session: Session):
    client = make_api_client(db_session)
    term_id = _create_term(client)
    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": term_id,
            "block_id": "00000000-0000-0000-0000-000000000099",
            "base_schedule_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "The requested block was not found."


def test_submit_ga_base_schedule_not_found_returns_404(db_session: Session):
    from app.tests.support.scheduling import BLOCK_ID, TERM_ID, seed_scheduling_problem

    seed_scheduling_problem(db_session)
    client = make_api_client(db_session)
    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(TERM_ID),
            "block_id": str(BLOCK_ID),
            "base_schedule_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "The referenced base schedule was not found."


def test_enqueue_block_ga_is_durable_and_snapshots_context(db_session: Session):
    from app.tests.integration.test_block_generation_context_repository import (
        seed_base_schedule,
        seed_second_block,
    )
    from app.tests.support.scheduling import (
        BLOCK_ID,
        SCHEDULE_ID,
        TERM_ID,
        seed_scheduling_problem,
    )

    seed_scheduling_problem(db_session)
    seed_second_block(db_session, ready_for_generation=True)
    seed_base_schedule(db_session)

    client = make_api_client(db_session)
    accepted = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(TERM_ID),
            "block_id": str(BLOCK_ID),
            "base_schedule_id": str(SCHEDULE_ID),
        },
    )
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["kind"] == "ga"
    assert body["status"] == "queued"

    persisted = JobRepository(db_session).get(UUID(body["job_id"]))
    assert persisted is not None
    assert persisted.status is JobStatus.QUEUED
    assert "block_context" in persisted.request
    from app.services.jobs import decode_block_generation_context

    decoded = decode_block_generation_context(persisted.request["block_context"])
    assert decoded.selected_block_id == BLOCK_ID


def test_committee_ga_config_override_is_refused_without_enqueuing(db_session: Session):
    client = make_api_client(db_session)
    term_id = _ready_term(db_session)

    refused = client.post(
        "/api/v1/jobs/ga", json={"term_id": term_id, "config": {"population_size": 8}}
    )

    assert refused.status_code == 403
    assert refused.json()["code"] == "GA_CONFIG_OVERRIDE_FORBIDDEN"
    assert db_session.scalar(select(func.count()).select_from(ExecutionJob)) == 0
    # Omitting config (or sending null) still runs with the saved configuration.
    assert client.post("/api/v1/jobs/ga", json={"term_id": term_id}).status_code == 202
    assert client.post(
        "/api/v1/jobs/ga", json={"term_id": term_id, "config": None}
    ).status_code == 202


def test_administrator_ga_config_override_reaches_the_job(db_session: Session):
    client = make_api_client(
        db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="administrator"
    )
    term_id = _ready_term(db_session)

    accepted = client.post(
        "/api/v1/jobs/ga", json={"term_id": term_id, "config": {"population_size": 8}}
    )

    assert accepted.status_code == 202
    job = db_session.get(ExecutionJob, accepted.json()["job_id"])
    assert job.request["config"]["population_size"] == 8
