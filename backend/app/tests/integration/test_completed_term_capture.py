"""CHR-110 / CHR-ING-002 completed-term capture on disposable PostgreSQL 17."""

from datetime import UTC, datetime, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    AppUser,
    BlockSection,
    BlockSectionOffering,
    ExecutionJob,
    ExtractedRule,
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    JobStatus,
    JobType,
    Room,
    RuleSet,
    RuleSetThreshold,
    Schedule,
    ScheduleRecord,
    ScheduleType,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
    UserRole,
)
from app.schemas.historical_capture import HistoricalCaptureRequest
from app.services.arm.mining import RuleMiner
from app.services.ingestion.completed_term import capture_completed_term
from app.tests.integration.alembic_support import run_alembic
from app.tests.support.api import make_api_client, make_client, make_user
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    OFFERING_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

FINALIZED_AT = datetime(2024, 5, 15, 12, tzinfo=UTC)
SOURCE_RECORD_ID = UUID("00000000-0000-0000-0000-000000000020")
SECOND_RECORD_ID = UUID("00000000-0000-0000-0000-000000000010")
SECOND_BLOCK_ID = UUID("00000000-0000-0000-0000-000000000011")
SECOND_CLASS_ID = UUID("00000000-0000-0000-0000-000000000012")
SECOND_OFFERING_ID = UUID("00000000-0000-0000-0000-000000000013")
OTHER_TERM_ID = UUID("00000000-0000-0000-0000-000000000098")
OTHER_SCHEDULE_ID = UUID("00000000-0000-0000-0000-000000000099")
CAPTURE_URL = f"/api/v1/terms/{TERM_ID}/historical-snapshot"
CAPTURE_PAYLOAD = {
    "schedule_id": str(SCHEDULE_ID),
    "confirm_completed_and_approved": True,
}


@pytest.fixture
def owned_capture_engine(migrated_engine):
    """Committed source rows and independent connections, confined to an owned DB."""
    name = f"chronos_capture_{uuid4().hex}_test"
    with migrated_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = migrated_engine.url.set(database=name)
    engine = create_engine(url)
    try:
        run_alembic(url.render_as_string(hide_password=False), "upgrade", "head")
        yield engine
    finally:
        engine.dispose()
        with migrated_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _seed_completed_source(db_session: Session) -> ScheduleRecord:
    seed_scheduling_problem(db_session)
    term = db_session.get(Term, TERM_ID)
    term.academic_year = "2023-2024"
    term.is_active = False
    db_session.get(TimeSlot, TIME_SLOT_ID).label = "LEC 08:00-09:30"
    db_session.add(
        Schedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            schedule_type=ScheduleType.MASTER,
            finalized_at=FINALIZED_AT,
        )
    )
    db_session.flush()
    record = ScheduleRecord(
        record_id=SOURCE_RECORD_ID,
        schedule_id=SCHEDULE_ID,
        term_id=TERM_ID,
        class_id=CLASS_ID,
        faculty_id=FACULTY_ID,
        subject_code="<SUBJECT-1>",
        room_code="<ROOM-1>",
        meeting_days="M",
        time_slot_id=TIME_SLOT_ID,
        time_slot="LEC 12:00-13:30",
        is_historical=False,
    )
    db_session.add(record)
    db_session.flush()
    return record


@pytest.fixture
def source_record(db_session: Session) -> ScheduleRecord:
    return _seed_completed_source(db_session)


@pytest.fixture
def admin_client(db_session: Session):
    return make_api_client(db_session, role=UserRole.DEPARTMENT_ADMINISTRATOR)


def _rows(db: Session, model) -> list[dict]:
    table = model.__table__
    return [
        dict(row)
        for row in db.execute(
            select(table).order_by(*table.primary_key.columns)
        ).mappings()
    ]


def _history_counts(db: Session) -> tuple[int, int]:
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    )


def _add_second_class(
    db: Session, *, shared: bool = True, assigned: bool = True, days: str = "M"
) -> ScheduleRecord | None:
    original_block = db.get(BlockSection, BLOCK_ID)
    db.add(
        BlockSection(
            block_id=SECOND_BLOCK_ID,
            term_id=TERM_ID,
            program_code=original_block.program_code,
            year_level=original_block.year_level,
            section_letter="B",
            is_expanded=False,
        )
    )
    if not shared:
        db.add(
            SubjectOffering(
                offering_id=SECOND_OFFERING_ID,
                term_id=TERM_ID,
                subject_code="<SUBJECT-1>",
                group_no=2,
            )
        )
    db.flush()
    db.add(
        BlockSectionOffering(
            class_id=SECOND_CLASS_ID,
            block_id=SECOND_BLOCK_ID,
            offering_id=OFFERING_ID if shared else SECOND_OFFERING_ID,
            term_id=TERM_ID,
            subject_code="<SUBJECT-1>",
        )
    )
    db.flush()
    if not assigned:
        return None
    record = ScheduleRecord(
        record_id=SECOND_RECORD_ID,
        schedule_id=SCHEDULE_ID,
        term_id=TERM_ID,
        class_id=SECOND_CLASS_ID,
        faculty_id=FACULTY_ID,
        subject_code="<SUBJECT-1>",
        room_code="<ROOM-1>",
        meeting_days=days,
        time_slot_id=TIME_SLOT_ID,
        time_slot="<STALE-SLOT>",
        is_historical=False,
    )
    db.add(record)
    db.flush()
    return record


def test_admin_capture_round_trip_preserves_sources_and_does_not_mine(
    db_session, source_record, admin_client, monkeypatch
):
    approver_id = db_session.scalar(select(AppUser.user_id))
    db_session.add_all(
        [
            RuleSet(
                version=1,
                status="active",
                activated_at=FINALIZED_AT,
                notes="<EXISTING-RULE-SET>",
            ),
            ExecutionJob(
                job_type=JobType.ARM,
                status=JobStatus.QUEUED,
                requested_by=approver_id,
                request={"label": "<EXISTING-JOB>"},
                timeout_seconds=30,
                max_attempts=1,
            ),
        ]
    )
    db_session.flush()
    unchanged_models = (
        Term,
        Schedule,
        ScheduleRecord,
        TimeSlot,
        Faculty,
        Subject,
        Room,
        RuleSet,
        RuleSetThreshold,
        ExtractedRule,
        ExecutionJob,
    )
    before = {model: _rows(db_session, model) for model in unchanged_models}

    def forbid_mining(*_args, **_kwargs):
        pytest.fail("History capture must not start mining")

    monkeypatch.setattr(RuleMiner, "mine", forbid_mining)
    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()
    snapshot = db_session.scalars(select(HistoricalSnapshot)).one()
    record = db_session.scalars(select(HistoricalRecord)).one()
    assert body == {
        "snapshot_id": str(snapshot.snapshot_id),
        "source_term_id": str(TERM_ID),
        "source_schedule_id": str(SCHEDULE_ID),
        "approved_by_user_id": str(approver_id),
        "academic_year": "2023-2024",
        "term_number": 1,
        "captured_at": body["captured_at"],
        "record_count": 1,
    }
    assert datetime.fromisoformat(body["captured_at"]) == snapshot.captured_at
    assert snapshot.captured_at.utcoffset() is not None
    assert (snapshot.source_schedule_id, snapshot.approved_by_user_id) == (
        SCHEDULE_ID,
        approver_id,
    )
    assert (record.snapshot_id, record.source_record_id, record.source_faculty_id) == (
        snapshot.snapshot_id,
        SOURCE_RECORD_ID,
        FACULTY_ID,
    )
    assert (record.faculty_name, record.subject_code, record.subject_title) == (
        "<FACULTY-1>",
        "<SUBJECT-1>",
        "<SUBJECT-TITLE-1>",
    )
    assert (record.room_code, record.meeting_days, record.time_slot) == (
        "<ROOM-1>",
        "M",
        "08:00-09:30",
    )
    assert {model: _rows(db_session, model) for model in unchanged_models} == before
    read = admin_client.get(CAPTURE_URL)
    assert read.status_code == 200
    assert read.json() == body

    history_before = [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ]
    repeat = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert repeat.status_code == 409
    assert repeat.json()["code"] == "HISTORY_ALREADY_CAPTURED"
    assert [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ] == history_before


def test_capture_keeps_a_teacher_marked_inactive_after_the_term(
    db_session, source_record, admin_client
):
    # Retiring a teacher once the term ends must not make the term's own
    # finalized schedule unreadable: capture still names them.
    retiring = db_session.get(Faculty, source_record.faculty_id)
    retiring.is_active = False
    db_session.flush()

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 201, response.text
    records = admin_client.get(f"{CAPTURE_URL}/records").json()["items"]
    assert [item["faculty_name"] for item in records] == [retiring.full_name]


def test_an_archived_term_must_be_restored_before_capture(
    db_session, source_record, admin_client
):
    # An archived term that is then captured would be hidden from both the
    # history list and the archive list (CHR-240).
    term = db_session.get(Term, TERM_ID)
    term.is_archived = True
    db_session.flush()

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "TERM_SOFT_ARCHIVED"


def test_capture_names_a_retired_teacher_whose_saved_mask_is_stale(
    db_session, source_record, admin_client
):
    retiring = db_session.get(Faculty, source_record.faculty_id)
    retiring.is_active = False
    retiring.availability_mask = "1" * 99  # saved for a different slot grid
    db_session.flush()

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 409
    assert response.json()["code"] == "FACULTY_MASK_STALE"
    assert retiring.full_name in response.json()["message"]
    assert _history_counts(db_session) == (0, 0)


@pytest.mark.parametrize(
    ("role", "status", "code"),
    [
        (None, 401, "AUTHENTICATION_REQUIRED"),
        (UserRole.SCHEDULING_COMMITTEE, 403, "FORBIDDEN"),
    ],
)
def test_capture_and_read_require_an_administrator(
    db_session, source_record, role, status, code
):
    client = (
        make_client(db_session)
        if role is None
        else make_api_client(db_session, role=role)
    )

    for method in ("GET", "POST"):
        response = client.request(
            method, CAPTURE_URL, json=CAPTURE_PAYLOAD if method == "POST" else None
        )
        assert response.status_code == status
        assert response.json()["code"] == code
    records = client.get(f"{CAPTURE_URL}/records")
    assert records.status_code == status
    assert records.json()["code"] == code
    assert _history_counts(db_session) == (0, 0)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param('{"schedule_id":', id="malformed-json"),
        pytest.param({**CAPTURE_PAYLOAD, "schedule_id": "not-a-uuid"}, id="bad-uuid"),
        pytest.param(
            {**CAPTURE_PAYLOAD, "approved_by_user_id": str(TERM_ID)}, id="extra"
        ),
        pytest.param({"confirm_completed_and_approved": True}, id="missing-schedule"),
        pytest.param({"schedule_id": str(SCHEDULE_ID)}, id="missing-confirmation"),
        pytest.param(
            {**CAPTURE_PAYLOAD, "confirm_completed_and_approved": False}, id="false"
        ),
        pytest.param(
            {**CAPTURE_PAYLOAD, "confirm_completed_and_approved": "true"}, id="string"
        ),
    ],
)
def test_invalid_capture_requests_are_422_without_history(
    db_session, source_record, admin_client, payload
):
    if isinstance(payload, str):
        response = admin_client.post(
            CAPTURE_URL, content=payload, headers={"Content-Type": "application/json"}
        )
    else:
        response = admin_client.post(CAPTURE_URL, json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert _history_counts(db_session) == (0, 0)


@pytest.mark.parametrize("missing", ["term", "schedule", "schedule-in-other-term"])
def test_missing_or_wrong_term_capture_source_is_404(
    db_session, source_record, admin_client, missing
):
    url, payload = CAPTURE_URL, CAPTURE_PAYLOAD.copy()
    if missing == "term":
        url = f"/api/v1/terms/{OTHER_TERM_ID}/historical-snapshot"
    else:
        payload["schedule_id"] = str(OTHER_SCHEDULE_ID)
        if missing == "schedule-in-other-term":
            db_session.add(
                Term(
                    term_id=OTHER_TERM_ID,
                    academic_year="2024-2025",
                    term_number=1,
                    is_active=False,
                )
            )
            db_session.flush()
            db_session.add(
                Schedule(
                    schedule_id=OTHER_SCHEDULE_ID,
                    term_id=OTHER_TERM_ID,
                    schedule_type=ScheduleType.MASTER,
                    finalized_at=FINALIZED_AT,
                )
            )
            db_session.flush()

    response = admin_client.post(url, json=payload)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert _history_counts(db_session) == (0, 0)


@pytest.mark.parametrize(
    ("term_id", "code"),
    [(TERM_ID, "HISTORY_NOT_CAPTURED"), (OTHER_TERM_ID, "NOT_FOUND")],
)
def test_read_of_missing_history_or_term_is_404(
    db_session, source_record, admin_client, term_id, code
):
    response = admin_client.get(f"/api/v1/terms/{term_id}/historical-snapshot")

    assert response.status_code == 404
    assert response.json()["code"] == code
    records = admin_client.get(f"/api/v1/terms/{term_id}/historical-snapshot/records")
    assert records.status_code == 404
    assert records.json()["code"] == code
    assert _history_counts(db_session) == (0, 0)


def test_read_prefers_source_term_provenance_over_a_colliding_period(
    db_session, admin_client
):
    approver_id = db_session.scalar(select(AppUser.user_id))
    term = Term(
        term_id=TERM_ID, academic_year="2022-2023", term_number=1, is_active=False
    )
    period_match = HistoricalSnapshot(
        snapshot_id=UUID("00000000-0000-0000-0000-000000000030"),
        source_term_id=OTHER_TERM_ID,
        source_schedule_id=OTHER_SCHEDULE_ID,
        approved_by_user_id=None,
        academic_year="2023-2024",
        term_number=1,
        captured_at=FINALIZED_AT,
    )
    # Seed the period match first to expose an unordered OR lookup.
    db_session.add_all([term, period_match])
    db_session.flush()
    source_match = HistoricalSnapshot(
        snapshot_id=UUID("00000000-0000-0000-0000-000000000031"),
        source_term_id=TERM_ID,
        source_schedule_id=SCHEDULE_ID,
        approved_by_user_id=approver_id,
        academic_year="2022-2023",
        term_number=1,
        captured_at=FINALIZED_AT,
    )
    db_session.add(source_match)
    db_session.flush()
    term.academic_year = "2023-2024"
    db_session.flush()
    db_session.expire_all()

    response = admin_client.get(CAPTURE_URL)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "snapshot_id": str(source_match.snapshot_id),
        "source_term_id": str(TERM_ID),
        "source_schedule_id": str(SCHEDULE_ID),
        "approved_by_user_id": str(approver_id),
        "academic_year": "2022-2023",
        "term_number": 1,
        "captured_at": "2024-05-15T12:00:00Z",
        "record_count": 0,
    }
    assert _history_counts(db_session) == (2, 0)


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("active", "TERM_NOT_COMPLETED"),
        ("routine", "CAPTURE_REQUIRES_MASTER"),
        ("unfinalized", "SCHEDULE_NOT_FINALIZED"),
        ("empty", "INVALID_CAPTURE_SOURCE"),
        ("historical-source", "INVALID_CAPTURE_SOURCE"),
        ("missing-class", "SCHEDULE_NOT_CAPTUREABLE"),
        ("unplaced", "SCHEDULE_NOT_CAPTUREABLE"),
        ("room-changed", "SCHEDULE_NOT_CAPTUREABLE"),
        ("faculty-changed", "SCHEDULE_NOT_CAPTUREABLE"),
    ],
)
def test_uncaptureable_sources_are_409_without_partial_history(
    db_session, source_record, admin_client, state, code
):
    if state == "active":
        db_session.get(Term, TERM_ID).is_active = True
    elif state == "routine":
        db_session.get(Schedule, SCHEDULE_ID).schedule_type = ScheduleType.ROUTINE
    elif state == "unfinalized":
        db_session.get(Schedule, SCHEDULE_ID).finalized_at = None
    elif state == "empty":
        db_session.delete(source_record)
    elif state == "historical-source":
        source_record.is_historical = True
    elif state == "missing-class":
        _add_second_class(db_session, assigned=False)
    elif state == "unplaced":
        source_record.room_code = None
    elif state == "room-changed":
        db_session.get(Room, "<ROOM-1>").capacity = 19
    elif state == "faculty-changed":
        db_session.get(Faculty, FACULTY_ID).availability_mask = "0"
    db_session.flush()

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == code
    if state == "missing-class":
        assert body["details"] == {
            "required_class_count": 2,
            "assigned_class_count": 1,
            "hard_conflict_count": 0,
        }
    elif state == "unplaced":
        assert body["details"] == {
            "required_class_count": 1,
            "assigned_class_count": 0,
            "hard_conflict_count": 0,
        }
    elif state in {"room-changed", "faculty-changed"}:
        assert body["details"] == {
            "required_class_count": 1,
            "assigned_class_count": 1,
            "hard_conflict_count": 1,
        }
        assert db_session.get(Schedule, SCHEDULE_ID).finalized_at == FINALIZED_AT
    assert _history_counts(db_session) == (0, 0)


@pytest.mark.parametrize(
    ("shared", "days", "status", "record_count"),
    [
        pytest.param(True, "M", 201, 1, id="shared-single-meeting"),
        pytest.param(True, "T", 409, 0, id="shared-nonoverlapping-placements"),
        pytest.param(False, "T", 201, 2, id="distinct-offerings"),
    ],
)
def test_capture_counts_teaching_meetings_by_offering(
    db_session, source_record, admin_client, shared, days, status, record_count
):
    second = _add_second_class(db_session, shared=shared, days=days)
    db_session.commit()  # seeded state survives a refused, audited capture
    assert second.record_id < source_record.record_id
    assert db_session.get(Room, "<ROOM-1>").capacity == 40
    before = _rows(db_session, ScheduleRecord)

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == status, response.text
    if status == 409:
        # The validator now rejects split shared sessions before capture's own
        # placement check runs.
        assert response.json()["code"] == "SCHEDULE_NOT_CAPTUREABLE"
        assert response.json()["details"]["hard_conflict_count"] == 1
        assert _history_counts(db_session) == (0, 0)
    else:
        assert response.json()["record_count"] == record_count
        assert _history_counts(db_session) == (1, record_count)
        records = db_session.scalars(
            select(HistoricalRecord).order_by(HistoricalRecord.source_record_id)
        ).all()
        assert [row.source_record_id for row in records] == (
            [SECOND_RECORD_ID] if shared else [SECOND_RECORD_ID, SOURCE_RECORD_ID]
        )
        assert {row.subject_code for row in records} == {"<SUBJECT-1>"}
        assert {row.time_slot for row in records} == {"08:00-09:30"}
    assert _rows(db_session, ScheduleRecord) == before


def test_source_edits_and_renames_cannot_change_captured_history(
    db_session, source_record, admin_client
):
    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert response.status_code == 201, response.text
    before = [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ]

    db_session.get(Faculty, FACULTY_ID).full_name = "<RENAMED-FACULTY-1>"
    db_session.get(Subject, "<SUBJECT-1>").subject_title = "<RETITLED-SUBJECT-1>"
    db_session.get(Room, "<ROOM-1>").capacity = 1
    term = db_session.get(Term, TERM_ID)
    term.academic_year, term.term_number = "2024-2025", 2
    slot = db_session.get(TimeSlot, TIME_SLOT_ID)
    slot.label, slot.start_time, slot.end_time = (
        "LEC 11:00-12:30",
        time(11),
        time(12, 30),
    )
    source_record.meeting_days, source_record.time_slot = "T", "<EDITED-SLOT>"
    db_session.flush()
    db_session.expire_all()

    assert [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ] == before
    read = admin_client.get(CAPTURE_URL)
    assert read.status_code == 200
    assert read.json() == response.json()


@pytest.mark.parametrize(
    "source_term_id",
    [TERM_ID, OTHER_TERM_ID],
    ids=[
        "bootstrap-source-term",
        "bootstrap-same-period",
    ],
)
def test_bootstrap_history_is_readable_but_cannot_be_recaptured(
    db_session, source_record, admin_client, source_term_id
):
    snapshot = HistoricalSnapshot(
        source_term_id=source_term_id,
        source_schedule_id=None,
        approved_by_user_id=None,
        academic_year="2023-2024",
        term_number=1,
        captured_at=FINALIZED_AT,
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        HistoricalRecord(
            snapshot_id=snapshot.snapshot_id,
            source_record_id=None,
            source_faculty_id=FACULTY_ID,
            faculty_name="<FACULTY-1>",
            subject_code="<SUBJECT-1>",
            subject_title="<SUBJECT-TITLE-1>",
            room_code=None,
            meeting_days="M",
            time_slot="08:00-09:30",
        )
    )
    db_session.commit()  # seeded state survives a refused, audited capture
    before = [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ]

    read = admin_client.get(CAPTURE_URL)

    assert read.status_code == 200
    assert read.json() == {
        "snapshot_id": str(snapshot.snapshot_id),
        "source_term_id": str(source_term_id),
        "source_schedule_id": None,
        "approved_by_user_id": None,
        "academic_year": "2023-2024",
        "term_number": 1,
        "captured_at": "2024-05-15T12:00:00Z",
        "record_count": 1,
    }
    records = admin_client.get(f"{CAPTURE_URL}/records")
    assert records.status_code == 200
    assert [
        {key: value for key, value in item.items() if key != "historical_record_id"}
        for item in records.json()["items"]
    ] == [
        {
            "source_faculty_id": str(FACULTY_ID),
            "faculty_name": "<FACULTY-1>",
            "subject_code": "<SUBJECT-1>",
            "subject_title": "<SUBJECT-TITLE-1>",
            "room_code": None,
            "meeting_days": "M",
            "time_slot": "08:00-09:30",
        }
    ]
    refused = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert refused.status_code == 409
    assert refused.json()["code"] == "HISTORY_ALREADY_CAPTURED"
    selection = admin_client.post(f"/api/v1/terms/{TERM_ID}/select")
    assert selection.status_code == 409
    assert selection.json()["code"] == "TERM_ARCHIVED"
    assert db_session.get(Term, TERM_ID).is_active is False
    assert [
        _rows(db_session, model)
        for model in (
            HistoricalSnapshot,
            HistoricalRecord,
        )
    ] == before


def test_archived_term_cannot_replace_the_current_active_term(
    db_session, source_record, admin_client
):
    captured = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert captured.status_code == 201, captured.text
    db_session.add(
        Term(
            term_id=OTHER_TERM_ID,
            academic_year="2024-2025",
            term_number=1,
            is_active=True,
        )
    )
    db_session.flush()
    before = _rows(db_session, Term)

    response = admin_client.post(f"/api/v1/terms/{TERM_ID}/select")

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_ARCHIVED"
    assert _rows(db_session, Term) == before
    assert _history_counts(db_session) == (1, 1)


def test_captured_term_cannot_be_renamed_but_same_values_succeed(
    db_session, source_record, admin_client
):
    captured = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert captured.status_code == 201, captured.text
    term = db_session.get(Term, TERM_ID)
    year, number = term.academic_year, term.term_number
    url = f"/api/v1/terms/{TERM_ID}"

    renamed = admin_client.patch(url, json={"academic_year": "2017-2018"})
    renumbered = admin_client.patch(url, json={"term_number": 2})

    for response in (renamed, renumbered):
        assert response.status_code == 409
        assert response.json()["code"] == "TERM_ARCHIVED"
    db_session.expire_all()
    term = db_session.get(Term, TERM_ID)
    assert (term.academic_year, term.term_number) == (year, number)
    same = admin_client.patch(url, json={"academic_year": year, "term_number": number})
    assert same.status_code == 200


def test_uncaptured_term_can_still_be_renamed(db_session, source_record, admin_client):
    response = admin_client.patch(
        f"/api/v1/terms/{TERM_ID}", json={"academic_year": "2017-2018"}
    )

    assert response.status_code == 200
    assert response.json()["academic_year"] == "2017-2018"


def test_term_captured_by_period_only_cannot_be_renamed(
    db_session, source_record, admin_client
):
    db_session.add(
        HistoricalSnapshot(
            source_term_id=OTHER_TERM_ID,
            source_schedule_id=None,
            approved_by_user_id=None,
            academic_year="2023-2024",
            term_number=1,
            captured_at=FINALIZED_AT,
        )
    )
    db_session.commit()

    response = admin_client.patch(
        f"/api/v1/terms/{TERM_ID}", json={"academic_year": "2017-2018"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_ARCHIVED"
    db_session.expire_all()
    assert db_session.get(Term, TERM_ID).academic_year == "2023-2024"


def test_uncaptured_term_cannot_be_renamed_into_a_captured_period(
    db_session, source_record, admin_client
):
    db_session.add(
        HistoricalSnapshot(
            source_term_id=OTHER_TERM_ID,
            source_schedule_id=None,
            approved_by_user_id=None,
            academic_year="2019-2020",
            term_number=1,
            captured_at=FINALIZED_AT,
        )
    )
    db_session.commit()

    response = admin_client.patch(
        f"/api/v1/terms/{TERM_ID}", json={"academic_year": "2019-2020"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TERM_ARCHIVED"
    db_session.expire_all()
    assert db_session.get(Term, TERM_ID).academic_year == "2023-2024"


def test_captured_term_empty_and_same_value_patches_succeed(
    db_session, source_record, admin_client
):
    captured = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert captured.status_code == 201, captured.text
    url = f"/api/v1/terms/{TERM_ID}"

    assert admin_client.patch(url, json={}).status_code == 200
    same = admin_client.patch(url, json={"term_number": 1})
    assert same.status_code == 200


@pytest.mark.parametrize("pending_state", ["new", "dirty", "deleted"])
def test_capture_refuses_unflushed_caller_changes_without_committing_them(
    db_session, source_record, admin_client, pending_state
):
    approver_id = db_session.scalar(select(AppUser.user_id))
    before = _rows(db_session, ScheduleRecord)
    transaction = db_session.get_transaction()
    pending = source_record
    if pending_state == "new":
        pending = Faculty(
            full_name="<PENDING-FACULTY>", is_full_time=False, availability_mask="1"
        )
        db_session.add(pending)
    elif pending_state == "dirty":
        pending.time_slot = "<PENDING-SLOT>"
    else:
        db_session.delete(pending)

    with pytest.raises(APIError) as error:
        capture_completed_term(
            db_session,
            TERM_ID,
            HistoricalCaptureRequest.model_validate(CAPTURE_PAYLOAD),
            approved_by_user_id=approver_id,
        )

    assert (error.value.status_code, error.value.code) == (
        409,
        "CAPTURE_PENDING_CHANGES",
    )
    assert pending in getattr(db_session, pending_state)
    assert db_session.get_transaction() is transaction
    if pending_state == "new":
        assert pending.faculty_id is None
    with db_session.no_autoflush:
        assert _history_counts(db_session) == (0, 0)
        assert _rows(db_session, ScheduleRecord) == before


@pytest.mark.parametrize(("seconds", "microseconds"), [(1, 0), (0, 1)])
def test_non_minute_slot_boundaries_roll_back_the_snapshot_and_allow_retry(
    db_session, source_record, admin_client, seconds, microseconds
):
    slot = db_session.get(TimeSlot, TIME_SLOT_ID)
    # Keep the duration at 90 minutes so this reaches the denormalization guard.
    slot.start_time = time(8, 0, seconds, microseconds)
    slot.end_time = time(9, 30, seconds, microseconds)
    db_session.flush()

    response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "INVALID_CAPTURE_SOURCE"
    assert _history_counts(db_session) == (0, 0)
    slot.start_time, slot.end_time = time(8), time(9, 30)
    db_session.flush()
    retry = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert retry.status_code == 201, retry.text
    assert _history_counts(db_session) == (1, 1)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (IntegrityError, "HISTORY_CAPTURE_CONFLICT"),
        (OperationalError, "CAPTURE_SOURCE_CHANGED"),
    ],
)
def test_database_failure_after_inserts_leaves_no_partial_capture(
    db_session, source_record, admin_client, failure, code
):
    # Commit only the fixture savepoint, preserving it across the service rollback.
    db_session.commit()
    before = _rows(db_session, ScheduleRecord)
    inserted_counts = []

    def fail_after_insert(_mapper, connection, _record):
        inserted_counts.append(
            tuple(
                connection.scalar(select(func.count()).select_from(model))
                for model in (HistoricalSnapshot, HistoricalRecord)
            )
        )
        raise failure(
            "synthetic history insert failure", {}, RuntimeError("synthetic failure")
        )

    event.listen(HistoricalRecord, "after_insert", fail_after_insert)
    try:
        response = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    finally:
        event.remove(HistoricalRecord, "after_insert", fail_after_insert)

    assert inserted_counts == [(1, 1)]
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    assert _history_counts(db_session) == (0, 0)
    assert _rows(db_session, ScheduleRecord) == before
    retry = admin_client.post(CAPTURE_URL, json=CAPTURE_PAYLOAD)
    assert retry.status_code == 201, retry.text
    assert _history_counts(db_session) == (1, 1)


def test_early_term_lock_failure_is_sanitized_and_rolls_back(owned_capture_engine):
    source_models = (Term, Schedule, ScheduleRecord, TimeSlot)
    with Session(owned_capture_engine) as seed:
        _seed_completed_source(seed)
        approver_id = make_user(seed, role=UserRole.DEPARTMENT_ADMINISTRATOR).user_id
        before = {model: _rows(seed, model) for model in source_models}
        seed.commit()
    request = HistoricalCaptureRequest.model_validate(CAPTURE_PAYLOAD)

    with (
        Session(owned_capture_engine) as blocker,
        Session(owned_capture_engine) as capturing,
    ):
        assert (
            blocker.scalar(
                select(Term).where(Term.term_id == TERM_ID).with_for_update()
            )
            is not None
        )
        capturing.execute(text("SET LOCAL lock_timeout = '100ms'"))

        with pytest.raises(APIError) as error:
            capture_completed_term(
                capturing, TERM_ID, request, approved_by_user_id=approver_id
            )

        assert isinstance(error.value.__cause__, OperationalError)
        assert (error.value.status_code, error.value.code) == (
            409,
            "CAPTURE_SOURCE_CHANGED",
        )
        assert error.value.message == (
            "The capture transaction failed; refresh its source state before retrying."
        )
        assert error.value.details == {}
        assert not capturing.in_transaction()
        assert _history_counts(capturing) == (0, 0)
        assert {model: _rows(capturing, model) for model in source_models} == before


def test_provenance_migration_preserves_old_snapshots_and_immutability(
    owned_capture_engine,
):
    url = owned_capture_engine.url.render_as_string(hide_password=False)
    run_alembic(url, "downgrade", "a4e8c2f6b0d3")
    snapshot_id = UUID("00000000-0000-0000-0000-000000000081")
    record_id = UUID("00000000-0000-0000-0000-000000000082")
    with owned_capture_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO historical_snapshot (snapshot_id, source_term_id, academic_year, term_number) VALUES (:snapshot, :term, '2023-2024', 1)"
            ),
            {"snapshot": snapshot_id, "term": TERM_ID},
        )
        connection.execute(
            text(
                "INSERT INTO historical_record (historical_record_id, snapshot_id, source_faculty_id, faculty_name, subject_code, subject_title, meeting_days, time_slot) VALUES (:record, :snapshot, :faculty, 'LEGACY SYNTHETIC FACULTY', 'LEGACY-SUBJECT', 'Legacy Synthetic Title', 'M', '08:00-09:30')"
            ),
            {"record": record_id, "snapshot": snapshot_id, "faculty": FACULTY_ID},
        )
        snapshot_before = dict(
            connection.execute(
                text("SELECT * FROM historical_snapshot WHERE snapshot_id=:snapshot"),
                {"snapshot": snapshot_id},
            )
            .mappings()
            .one()
        )
        record_before = dict(
            connection.execute(
                text(
                    "SELECT * FROM historical_record WHERE historical_record_id=:record"
                ),
                {"record": record_id},
            )
            .mappings()
            .one()
        )
    run_alembic(url, "upgrade", "head")
    with owned_capture_engine.connect() as connection:
        snapshot_after = dict(
            connection.execute(
                text("SELECT * FROM historical_snapshot WHERE snapshot_id=:snapshot"),
                {"snapshot": snapshot_id},
            )
            .mappings()
            .one()
        )
        record_after = dict(
            connection.execute(
                text(
                    "SELECT * FROM historical_record WHERE historical_record_id=:record"
                ),
                {"record": record_id},
            )
            .mappings()
            .one()
        )
    assert snapshot_after.pop("source_schedule_id") is None
    assert snapshot_after.pop("approved_by_user_id") is None
    assert snapshot_after == snapshot_before
    assert record_after == record_before
    with (
        pytest.raises(IntegrityError, match="historical data is immutable"),
        owned_capture_engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE historical_snapshot SET approved_by_user_id=:actor WHERE snapshot_id=:snapshot"
            ),
            {"actor": FACULTY_ID, "snapshot": snapshot_id},
        )
    run_alembic(url, "check")


def test_term_selection_handles_target_disappearing_before_lock(
    db_session, source_record, monkeypatch
):
    from app.services.catalog import terms as service

    values = iter([None, None])
    monkeypatch.setattr(db_session, "scalar", lambda *_args, **_kwargs: next(values))
    with pytest.raises(APIError) as error:
        service.select_term(db_session, TERM_ID)
    assert (error.value.status_code, error.value.code) == (404, "NOT_FOUND")
