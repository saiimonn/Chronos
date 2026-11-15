"""Unit tests for CHR-76 / CHR-SCH-006 preview-and-confirm Block replacement APIs."""

import json
from dataclasses import replace
from datetime import time
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.core.errors import APIError
from app.domain import (
    BlockClassDTO,
    BlockGenerationContext,
    BlockSectionDTO,
    CourseProgramDTO,
    FacultyDTO,
    RoomDTO,
    RoomType,
    SchedulingProblem,
    SubjectDTO,
    SubjectType,
    TermDTO,
    TimeSlotDTO,
    build_offering_block_ids,
)
from app.models import (
    BlockSection,
    ExecutionJob,
    Schedule,
    ScheduleRecord,
    ScheduleType,
)
from app.models.execution_job import JobStatus, JobType
from app.services.ga.config import GAConfig
from app.services.jobs.codecs import encode_block_ga_payload
from app.schemas.schedule import (
    BlockReplacementConfirmRequest,
    BlockReplacementPreviewRequest,
    ReplacementAssignmentPayload,
)
from app.services.schedules import (
    _compute_assignments_hash,
    _compute_schedule_hash,
    _compute_term_inputs_hash,
    _create_preview_token,
    _verify_preview_token,
    confirm_block_replacement,
    preview_block_replacement,
)

TERM_ID = UUID("00000000-0000-0000-0000-000000000001")
BLOCK_A_ID = UUID("00000000-0000-0000-0000-000000000011")
BLOCK_B_ID = UUID("00000000-0000-0000-0000-000000000022")

CLASS_A1_ID = UUID("00000000-0000-0000-0000-000000000101")
CLASS_A2_ID = UUID("00000000-0000-0000-0000-000000000102")
CLASS_B1_ID = UUID("00000000-0000-0000-0000-000000000201")

FACULTY_1_ID = UUID("00000000-0000-0000-0000-000000000301")
FACULTY_2_ID = UUID("00000000-0000-0000-0000-000000000302")

TIME_SLOT_1_ID = UUID("00000000-0000-0000-0000-000000000401")
TIME_SLOT_2_ID = UUID("00000000-0000-0000-0000-000000000402")

OFFERING_A1_ID = UUID("00000000-0000-0000-0000-000000000501")
OFFERING_A2_ID = UUID("00000000-0000-0000-0000-000000000502")
OFFERING_B1_ID = UUID("00000000-0000-0000-0000-000000000503")


def _make_problem() -> SchedulingProblem:
    classes = (
        BlockClassDTO(
            class_id=CLASS_A1_ID,
            block_id=BLOCK_A_ID,
            term_id=TERM_ID,
            offering_id=OFFERING_A1_ID,
            group_no=1,
            subject_code="CS101",
            program_code="BSCS",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        BlockClassDTO(
            class_id=CLASS_A2_ID,
            block_id=BLOCK_A_ID,
            term_id=TERM_ID,
            offering_id=OFFERING_A2_ID,
            group_no=1,
            subject_code="CS102",
            program_code="BSCS",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        BlockClassDTO(
            class_id=CLASS_B1_ID,
            block_id=BLOCK_B_ID,
            term_id=TERM_ID,
            offering_id=OFFERING_B1_ID,
            group_no=1,
            subject_code="IT101",
            program_code="BSIT",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
    )
    return SchedulingProblem(
        term=TermDTO(
            term_id=TERM_ID,
            academic_year="2026-2027",
            term_number=1,
            is_active=True,
        ),
        faculty=(
            FacultyDTO(
                faculty_id=FACULTY_1_ID,
                full_name="Prof. One",
                is_full_time=True,
                availability_mask="11",
            ),
            FacultyDTO(
                faculty_id=FACULTY_2_ID,
                full_name="Prof. Two",
                is_full_time=True,
                availability_mask="11",
            ),
        ),
        subjects=(
            SubjectDTO(
                subject_code="CS101",
                subject_title="Intro to CS",
                units=3,
                subject_type=SubjectType.LEC,
            ),
            SubjectDTO(
                subject_code="CS102",
                subject_title="Data Structures",
                units=3,
                subject_type=SubjectType.LEC,
            ),
            SubjectDTO(
                subject_code="IT101",
                subject_title="Intro to IT",
                units=3,
                subject_type=SubjectType.LEC,
            ),
        ),
        rooms=(
            RoomDTO(
                room_code="LB 101",
                building_name="Lawrence Bunzel",
                capacity=40,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
            RoomDTO(
                room_code="LB 102",
                building_name="Lawrence Bunzel",
                capacity=40,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
        ),
        programs=(
            CourseProgramDTO(program_code="BSCS", program_name="Computer Science"),
            CourseProgramDTO(program_code="BSIT", program_name="Information Technology"),
        ),
        classes=classes,
        time_slots=(
            TimeSlotDTO(
                time_slot_id=TIME_SLOT_1_ID,
                term_id=TERM_ID,
                label="07:30 - 09:00",
                start_time=time(7, 30),
                end_time=time(9, 0),
                ordinal=0,
            ),
            TimeSlotDTO(
                time_slot_id=TIME_SLOT_2_ID,
                term_id=TERM_ID,
                label="09:00 - 10:30",
                start_time=time(9, 0),
                end_time=time(10, 30),
                ordinal=1,
            ),
        ),
        offering_block_ids=build_offering_block_ids(classes),
    )


def _block_job_request(
    block_id: UUID = BLOCK_A_ID,
    term_id: UUID = TERM_ID,
    base_schedule_id: UUID | None = None,
) -> dict:
    """A per-Block GA job request built by the real encoder, so it cannot drift."""
    problem = _make_problem()
    block_classes = tuple(c for c in problem.classes if c.block_id == block_id)
    context = BlockGenerationContext(
        term=replace(problem.term, term_id=term_id),
        selected_block_id=block_id,
        selected_block=BlockSectionDTO(
            block_id=block_id,
            term_id=problem.term.term_id,
            program_code="BSCS",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=block_classes,
        locked_assignments=(),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=base_schedule_id,
    )
    return encode_block_ga_payload(context, GAConfig())


def _make_block(block_id: UUID = BLOCK_A_ID, term_id: UUID = TERM_ID) -> BlockSection:
    block = BlockSection(
        block_id=block_id,
        term_id=term_id,
        program_code="BSCS",
        year_level=1,
        section_letter="A",
        is_expanded=False,
    )
    return block


def _make_schedule(
    schedule_id: UUID | None = None,
    term_id: UUID = TERM_ID,
    finalized: bool = False,
) -> Schedule:
    sid = schedule_id or uuid4()
    sched = Schedule(
        schedule_id=sid,
        term_id=term_id,
        schedule_type=ScheduleType.ROUTINE.value,
    )
    if finalized:
        from datetime import datetime, timezone
        sched.finalized_at = datetime.now(timezone.utc)

    # Initial records: CLASS_A1 on TIME_SLOT_1, CLASS_B1 on TIME_SLOT_2
    r_a1 = ScheduleRecord(
        record_id=uuid4(),
        schedule_id=sid,
        term_id=term_id,
        class_id=CLASS_A1_ID,
        faculty_id=FACULTY_1_ID,
        subject_code="CS101",
        room_code="LB 101",
        meeting_days="MW",
        time_slot_id=TIME_SLOT_1_ID,
        time_slot="07:30 - 09:00",
        is_historical=False,
    )
    r_b1 = ScheduleRecord(
        record_id=uuid4(),
        schedule_id=sid,
        term_id=term_id,
        class_id=CLASS_B1_ID,
        faculty_id=FACULTY_2_ID,
        subject_code="IT101",
        room_code="LB 102",
        meeting_days="MW",
        time_slot_id=TIME_SLOT_2_ID,
        time_slot="09:00 - 10:30",
        is_historical=False,
    )
    sched.records = [r_a1, r_b1]
    return sched


# -------------------------------------------------------------------------
# Hashing & Token Tests
# -------------------------------------------------------------------------


def test_compute_schedule_hash_deterministic():
    schedule = _make_schedule()
    h1 = _compute_schedule_hash(schedule)
    h2 = _compute_schedule_hash(schedule)
    assert h1 == h2
    assert len(h1) == 64

    # Modifying an assignment changes the hash
    schedule.records[0].room_code = "LB 102"
    h3 = _compute_schedule_hash(schedule)
    assert h3 != h1


def test_compute_term_inputs_hash_deterministic():
    problem = _make_problem()
    h1 = _compute_term_inputs_hash(problem)
    h2 = _compute_term_inputs_hash(problem)
    assert h1 == h2
    assert len(h1) == 64


def test_preview_token_round_trip():
    schedule = _make_schedule()
    problem = _make_problem()
    s_hash = _compute_schedule_hash(schedule)
    t_hash = _compute_term_inputs_hash(problem)
    assignments = [
        ReplacementAssignmentPayload(
            class_id=CLASS_A1_ID,
            faculty_id=FACULTY_1_ID,
            room_code="LB 101",
            meeting_days="TTh",
            time_slot_id=TIME_SLOT_1_ID,
        )
    ]

    token = _create_preview_token(
        schedule.schedule_id,
        BLOCK_A_ID,
        s_hash,
        t_hash,
        assignments,
    )
    assert isinstance(token, str)
    assert "." in token

    decoded = _verify_preview_token(token, schedule.schedule_id, BLOCK_A_ID)
    assert decoded["schedule_id"] == str(schedule.schedule_id)
    assert decoded["block_id"] == str(BLOCK_A_ID)
    assert decoded["schedule_hash"] == s_hash
    assert decoded["term_inputs_hash"] == t_hash
    assert len(decoded["assignments"]) == 1


def test_verify_preview_token_rejects_tampering():
    schedule = _make_schedule()
    token = _create_preview_token(schedule.schedule_id, BLOCK_A_ID, "h1", "h2", [])

    # Tampered signature
    parts = token.split(".")
    tampered = f"{parts[0]}.wrongsig"
    with pytest.raises(APIError) as exc:
        _verify_preview_token(tampered, schedule.schedule_id, BLOCK_A_ID)
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_PREVIEW_TOKEN"


def test_verify_preview_token_rejects_mismatched_schedule_or_block():
    schedule = _make_schedule()
    token = _create_preview_token(schedule.schedule_id, BLOCK_A_ID, "h1", "h2", [])

    with pytest.raises(APIError) as exc:
        _verify_preview_token(token, uuid4(), BLOCK_A_ID)
    assert exc.value.status_code == 400

    with pytest.raises(APIError) as exc:
        _verify_preview_token(token, schedule.schedule_id, BLOCK_B_ID)
    assert exc.value.status_code == 400


# -------------------------------------------------------------------------
# Preview Endpoint Unit Tests
# -------------------------------------------------------------------------


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_preview_block_replacement_success_with_explicit_assignments(
    mock_get_schedule, mock_get_problem
):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    db = MagicMock()
    db.get.return_value = block

    # Class A1 is modified (meeting_days MW -> TTh), Class A2 is added
    payload = BlockReplacementPreviewRequest(
        assignments=[
            ReplacementAssignmentPayload(
                class_id=CLASS_A1_ID,
                faculty_id=FACULTY_1_ID,
                room_code="LB 101",
                meeting_days="TTh",
                time_slot_id=TIME_SLOT_1_ID,
            ),
            ReplacementAssignmentPayload(
                class_id=CLASS_A2_ID,
                faculty_id=FACULTY_2_ID,
                room_code="LB 102",
                meeting_days="TTh",
                time_slot_id=TIME_SLOT_2_ID,
            ),
        ]
    )

    response = preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)

    assert response["schedule_id"] == schedule.schedule_id
    assert response["block_id"] == BLOCK_A_ID
    assert "preview_token" in response
    assert len(response["proposed_assignments"]) == 2

    # Verify diff / changed_values
    diffs = {d["class_id"]: d for d in response["changed_values"]}
    assert diffs[CLASS_A1_ID]["status"] == "modified"
    assert "meeting_days" in diffs[CLASS_A1_ID]["changed_fields"]
    assert diffs[CLASS_A2_ID]["status"] == "added"

    # Completeness and validation
    validation = response["validation"]
    assert validation["required_class_count"] == 3
    assert validation["assigned_class_count"] == 3
    assert validation["completeness_percentage"] == 100.0
    assert validation["hard_conflict_count"] == 0

    # Verify db was NOT written to
    db.add.assert_not_called()
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_preview_block_replacement_with_completed_job_id(
    mock_get_schedule, mock_get_problem
):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    job_id = uuid4()
    job = ExecutionJob(
        job_id=job_id,
        job_type=JobType.GA.value,
        status=JobStatus.SUCCEEDED.value,
        request=_block_job_request(),
        result={
            "version": 6,
            "candidate": {
                "term_id": str(TERM_ID),
                "assignments": [
                    {
                        "class_id": str(CLASS_A1_ID),
                        "faculty_id": str(FACULTY_1_ID),
                        "room_code": "LB 101",
                        "meeting_days": "MW",
                        "time_slot_id": str(TIME_SLOT_1_ID),
                    }
                ],
            },
            "metrics": {},
        },
    )

    db = MagicMock()
    # db.get handles both BlockSection and ExecutionJob lookups
    def db_get_side_effect(model, ident):
        if model is BlockSection:
            return block
        if model is ExecutionJob:
            return job if ident == job_id else None
        return None

    db.get.side_effect = db_get_side_effect

    payload = BlockReplacementPreviewRequest(job_id=job_id)
    response = preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)

    assert response["schedule_id"] == schedule.schedule_id
    assert len(response["proposed_assignments"]) == 1


@patch("app.services.schedules.get_schedule")
def test_preview_rejects_finalized_schedule(mock_get_schedule):
    schedule = _make_schedule(finalized=True)
    mock_get_schedule.return_value = schedule

    db = MagicMock()
    payload = BlockReplacementPreviewRequest(
        assignments=[
            ReplacementAssignmentPayload(
                class_id=CLASS_A1_ID,
                faculty_id=FACULTY_1_ID,
                room_code="LB 101",
                meeting_days="MW",
                time_slot_id=TIME_SLOT_1_ID,
            )
        ]
    )

    with pytest.raises(APIError) as exc:
        preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)
    assert exc.value.status_code == 409
    assert exc.value.code == "SCHEDULE_FINALIZED"


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_preview_rejects_job_not_completed(mock_get_schedule, mock_get_problem):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    job_id = uuid4()
    job = ExecutionJob(
        job_id=job_id,
        job_type=JobType.GA.value,
        status=JobStatus.RUNNING.value,
        request=_block_job_request(),
    )

    db = MagicMock()
    db.get.side_effect = lambda model, ident: block if model is BlockSection else job

    payload = BlockReplacementPreviewRequest(job_id=job_id)
    with pytest.raises(APIError) as exc:
        preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)
    assert exc.value.status_code == 409
    assert exc.value.code == "JOB_NOT_COMPLETED"


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_preview_rejects_class_not_in_block(mock_get_schedule, mock_get_problem):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    db = MagicMock()
    db.get.return_value = block

    # Passing CLASS_B1_ID (belongs to Block B) for Block A replacement
    payload = BlockReplacementPreviewRequest(
        assignments=[
            ReplacementAssignmentPayload(
                class_id=CLASS_B1_ID,
                faculty_id=FACULTY_1_ID,
                room_code="LB 101",
                meeting_days="MW",
                time_slot_id=TIME_SLOT_1_ID,
            )
        ]
    )

    with pytest.raises(APIError) as exc:
        preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_ASSIGNMENT"


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_preview_rejects_duplicate_class_assignment(mock_get_schedule, mock_get_problem):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    db = MagicMock()
    db.get.return_value = block

    payload = BlockReplacementPreviewRequest(
        assignments=[
            ReplacementAssignmentPayload(
                class_id=CLASS_A1_ID,
                faculty_id=FACULTY_1_ID,
                room_code="LB 101",
                meeting_days="MW",
                time_slot_id=TIME_SLOT_1_ID,
            ),
            ReplacementAssignmentPayload(
                class_id=CLASS_A1_ID,
                faculty_id=FACULTY_2_ID,
                room_code="LB 102",
                meeting_days="TTh",
                time_slot_id=TIME_SLOT_2_ID,
            ),
        ]
    )

    with pytest.raises(APIError) as exc:
        preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)
    assert exc.value.status_code == 422
    assert exc.value.code == "DUPLICATE_ASSIGNMENT"


# -------------------------------------------------------------------------
# Confirmation Endpoint Unit Tests
# -------------------------------------------------------------------------


@patch("app.services.schedules._get_problem")
@patch("app.services.schedules.get_schedule")
def test_confirm_block_replacement_success_atomically_replaces_block_assignments(
    mock_get_schedule, mock_get_problem
):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_schedule.return_value = schedule
    mock_get_problem.return_value = problem

    s_hash = _compute_schedule_hash(schedule)
    t_hash = _compute_term_inputs_hash(problem)

    new_assignment = ReplacementAssignmentPayload(
        class_id=CLASS_A1_ID,
        faculty_id=FACULTY_1_ID,
        room_code="LB 101",
        meeting_days="TTh",
        time_slot_id=TIME_SLOT_1_ID,
    )

    token = _create_preview_token(
        schedule.schedule_id,
        BLOCK_A_ID,
        s_hash,
        t_hash,
        [new_assignment],
    )

    db = MagicMock()
    db.scalar.return_value = schedule
    db.get.return_value = block
    savepoint = MagicMock()
    db.begin_nested.return_value = savepoint

    payload = BlockReplacementConfirmRequest(preview_token=token)
    updated_sched, validation = confirm_block_replacement(
        db, schedule.schedule_id, BLOCK_A_ID, payload
    )

    # Verify db interactions:
    # 1. Old record for Class A1 deleted
    db.delete.assert_called_once_with(schedule.records[0])
    # 2. New record added, then the CHR-183 audit event in the same savepoint
    assert db.add.call_count == 2
    added_record, audit_event = (call.args[0] for call in db.add.call_args_list)
    assert added_record.class_id == CLASS_A1_ID
    assert added_record.meeting_days == "TTh"
    assert audit_event.operation == "schedule.block_replace"
    assert "preview_token" not in audit_event.details
    # 3. Savepoint and transaction committed
    savepoint.commit.assert_called_once()
    db.commit.assert_called_once()


@patch("app.services.schedules._get_problem")
def test_confirm_block_replacement_rejects_stale_preview_on_schedule_mutation(
    mock_get_problem
):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    mock_get_problem.return_value = problem

    # Token generated with original schedule hash
    s_hash = _compute_schedule_hash(schedule)
    t_hash = _compute_term_inputs_hash(problem)
    token = _create_preview_token(schedule.schedule_id, BLOCK_A_ID, s_hash, t_hash, [])

    # Underlying schedule is modified by someone else before confirmation
    schedule.records[1].meeting_days = "TTh"

    db = MagicMock()
    db.scalar.return_value = schedule
    db.get.return_value = block

    payload = BlockReplacementConfirmRequest(preview_token=token)
    with pytest.raises(APIError) as exc:
        confirm_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)

    assert exc.value.status_code == 409
    assert exc.value.code == "STALE_PREVIEW"
    # Prior assignments intact, no delete or commit
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@patch("app.services.schedules._get_problem")
def test_confirm_block_replacement_rejects_stale_preview_on_term_input_mutation(
    mock_get_problem
):
    schedule = _make_schedule()
    problem = _make_problem()
    block = _make_block()

    s_hash = _compute_schedule_hash(schedule)
    # Stale term inputs hash
    token = _create_preview_token(
        schedule.schedule_id, BLOCK_A_ID, s_hash, "stale_term_hash", []
    )

    mock_get_problem.return_value = problem

    db = MagicMock()
    db.scalar.return_value = schedule
    db.get.return_value = block

    payload = BlockReplacementConfirmRequest(preview_token=token)
    with pytest.raises(APIError) as exc:
        confirm_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)

    assert exc.value.status_code == 409
    assert exc.value.code == "STALE_PREVIEW"
    db.delete.assert_not_called()
    db.commit.assert_not_called()


def test_confirm_block_replacement_rejects_finalized_schedule():
    schedule = _make_schedule(finalized=True)
    db = MagicMock()
    db.scalar.return_value = schedule

    payload = BlockReplacementConfirmRequest(preview_token="dummy.token")
    with pytest.raises(APIError) as exc:
        confirm_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)

    assert exc.value.status_code == 409
    assert exc.value.code == "SCHEDULE_FINALIZED"


def _preview_job(request, job_type=JobType.GA.value):
    schedule = _make_schedule()
    job = ExecutionJob(
        job_id=uuid4(),
        job_type=job_type,
        status=JobStatus.SUCCEEDED.value,
        request=request,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, ident: (
        _make_block() if model is BlockSection else job
    )
    payload = BlockReplacementPreviewRequest(job_id=job.job_id)
    with patch("app.services.schedules.get_schedule", return_value=schedule), patch(
        "app.services.schedules._get_problem", return_value=_make_problem()
    ):
        with pytest.raises(APIError) as exc:
            preview_block_replacement(db, schedule.schedule_id, BLOCK_A_ID, payload)
    return exc.value, schedule


def _malformed(mutate):
    request = _block_job_request()
    mutate(request["block_context"]["context"])
    return request


@pytest.mark.parametrize(
    "request_, job_type",
    [
        ({"version": 6, "config": {}}, JobType.GA.value),
        ({"version": 6, "terms": []}, JobType.ARM.value),
        ({"block_context": None}, JobType.GA.value),
        ({"block_context": {"context": None}}, JobType.GA.value),
        (_malformed(lambda c: c.pop("selected_block")), JobType.GA.value),
        (
            _malformed(lambda c: c.update(selected_block_id="not-a-uuid")),
            JobType.GA.value,
        ),
    ],
)
def test_preview_rejects_non_block_or_malformed_job(request_, job_type):
    err, _ = _preview_job(request_, job_type)
    assert err.status_code == 400
    assert err.code == "INVALID_JOB"


def test_preview_rejects_job_for_another_block():
    err, _ = _preview_job(_block_job_request(block_id=BLOCK_B_ID))
    assert (err.status_code, err.code) == (400, "INVALID_JOB")
    assert "this block" in err.message


def test_preview_rejects_job_for_another_term():
    err, _ = _preview_job(_block_job_request(term_id=uuid4()))
    assert (err.status_code, err.code) == (400, "INVALID_JOB")
    assert "term" in err.message


def test_preview_rejects_job_for_another_base_schedule():
    err, _ = _preview_job(_block_job_request(base_schedule_id=uuid4()))
    assert (err.status_code, err.code) == (400, "INVALID_JOB")
    assert "against this schedule" in err.message
