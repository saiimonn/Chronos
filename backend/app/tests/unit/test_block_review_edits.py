"""Review-phase edits and generate-all context for the Master Schedule page.

A review edit is a preview of the Block's proposal with one class changed and
``strict_class_ids`` naming it: the independent validator must refuse it, with
the reason, whenever the changed class lands in a hard conflict with an
accepted assignment of another Block or with the rest of the proposal.

A generate-all batch passes earlier runs as ``context_job_ids``; their
candidates become extra locked context for the next Block's run.
"""

from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import APIError
from app.domain import LockedAssignment
from app.models import BlockSection, ExecutionJob
from app.models.execution_job import JobStatus, JobType
from app.schemas.jobs import SubmitGAJobRequest
from app.schemas.schedule import (
    BlockReplacementPreviewRequest,
    ReplacementAssignmentPayload,
)
from app.services.job_submission import GAContextJobError, add_proposal_locks
from app.services.jobs.codecs import decode_block_generation_context
from app.services.schedules import preview_block_replacement
from app.tests.unit.test_block_replacement import (
    BLOCK_A_ID,
    BLOCK_B_ID,
    CLASS_A1_ID,
    CLASS_A2_ID,
    CLASS_B1_ID,
    FACULTY_1_ID,
    FACULTY_2_ID,
    OFFERING_B1_ID,
    TERM_ID,
    TIME_SLOT_1_ID,
    TIME_SLOT_2_ID,
    _block_job_request,
    _make_block,
    _make_problem,
    _make_schedule,
)


def _assignment(class_id, faculty_id, room, days, slot):
    return ReplacementAssignmentPayload(
        class_id=class_id,
        faculty_id=faculty_id,
        room_code=room,
        meeting_days=days,
        time_slot_id=slot,
    )


def _preview(assignments, strict):
    """Block A preview against the schedule where Block B's IT101 is accepted
    on MW 09:00-10:30 in LB 102 with Prof. Two."""
    schedule = _make_schedule()
    db = MagicMock()
    db.get.return_value = _make_block()
    with (
        patch("app.services.schedules.get_schedule", return_value=schedule),
        patch("app.services.schedules._get_problem", return_value=_make_problem()),
    ):
        return preview_block_replacement(
            db,
            schedule.schedule_id,
            BLOCK_A_ID,
            BlockReplacementPreviewRequest(
                assignments=assignments, strict_class_ids=strict
            ),
        )


BASE_A2 = _assignment(CLASS_A2_ID, FACULTY_1_ID, "LB 101", "TTh", TIME_SLOT_1_ID)


def test_edit_without_conflict_is_accepted_and_tokenized():
    response = _preview(
        [_assignment(CLASS_A1_ID, FACULTY_1_ID, "LB 101", "MW", TIME_SLOT_1_ID), BASE_A2],
        [CLASS_A1_ID],
    )
    assert response["validation"]["hard_conflict_count"] == 0
    assert response["preview_token"]


def test_edit_double_booking_an_accepted_room_is_refused_with_reason():
    # LB 102 is taken on MW 09:00-10:30 by Block B's accepted class.
    with pytest.raises(APIError) as caught:
        _preview(
            [_assignment(CLASS_A1_ID, FACULTY_1_ID, "LB 102", "MW", TIME_SLOT_2_ID), BASE_A2],
            [CLASS_A1_ID],
        )
    error = caught.value
    assert (error.status_code, error.code) == (409, "PROPOSAL_EDIT_CONFLICT")
    assert error.message.startswith("The change was not applied.")
    codes = {conflict["code"] for conflict in error.details["conflicts"]}
    assert "ROOM_OVERLAP" in codes
    assert all(
        str(CLASS_A1_ID) in conflict["class_ids"]
        for conflict in error.details["conflicts"]
    )


def test_edit_double_booking_an_accepted_teacher_is_refused():
    # Prof. Two already teaches Block B's class on MW 09:00-10:30.
    with pytest.raises(APIError) as caught:
        _preview(
            [_assignment(CLASS_A1_ID, FACULTY_2_ID, "LB 101", "MW", TIME_SLOT_2_ID), BASE_A2],
            [CLASS_A1_ID],
        )
    codes = {conflict["code"] for conflict in caught.value.details["conflicts"]}
    assert caught.value.code == "PROPOSAL_EDIT_CONFLICT"
    assert "FACULTY_OVERLAP" in codes


def test_edit_clashing_with_the_rest_of_the_proposal_is_refused():
    # A2 sits on TTh 07:30-09:00; moving A1 there double-books Block A itself.
    with pytest.raises(APIError) as caught:
        _preview(
            [_assignment(CLASS_A1_ID, FACULTY_2_ID, "LB 102", "TTh", TIME_SLOT_1_ID), BASE_A2],
            [CLASS_A1_ID],
        )
    codes = {conflict["code"] for conflict in caught.value.details["conflicts"]}
    assert "COHORT_OVERLAP" in codes


def test_conflicts_not_touching_the_edited_class_do_not_refuse_it():
    # A2 double-books LB 102 with Block B, but only A1 was edited.
    response = _preview(
        [
            _assignment(CLASS_A1_ID, FACULTY_1_ID, "LB 101", "MW", TIME_SLOT_1_ID),
            _assignment(CLASS_A2_ID, FACULTY_1_ID, "LB 102", "MW", TIME_SLOT_2_ID),
        ],
        [CLASS_A1_ID],
    )
    assert response["validation"]["hard_conflict_count"] > 0


def test_without_strict_class_ids_a_conflicting_preview_is_still_returned():
    response = _preview(
        [_assignment(CLASS_A1_ID, FACULTY_1_ID, "LB 102", "MW", TIME_SLOT_2_ID), BASE_A2],
        None,
    )
    assert response["validation"]["hard_conflict_count"] > 0


@pytest.mark.parametrize(
    "strict, message",
    [
        ([CLASS_B1_ID], "does not belong"),
        ([CLASS_A2_ID], "no assignment"),
    ],
)
def test_strict_class_must_be_a_placed_class_of_the_block(strict, message):
    with pytest.raises(APIError) as caught:
        _preview(
            [_assignment(CLASS_A1_ID, FACULTY_1_ID, "LB 101", "MW", TIME_SLOT_1_ID)],
            strict,
        )
    assert caught.value.status_code == 422
    assert message in caught.value.message


# -------------------------------------------------------------------------
# Generate-all context
# -------------------------------------------------------------------------


def _context_job(block_id=BLOCK_B_ID, *, status=JobStatus.SUCCEEDED.value, **request):
    return ExecutionJob(
        job_id=uuid4(),
        job_type=JobType.GA.value,
        status=status,
        request=_block_job_request(block_id=block_id, **request),
        result={
            "candidate": {
                "assignments": [
                    {
                        "class_id": str(CLASS_B1_ID),
                        "faculty_id": str(FACULTY_2_ID),
                        "room_code": "LB 102",
                        "meeting_days": "TTh",
                        "time_slot_id": str(TIME_SLOT_1_ID),
                    }
                ]
            }
        },
    )


def _block_a_context(**request):
    return decode_block_generation_context(
        _block_job_request(block_id=BLOCK_A_ID, **request)["block_context"]
    )


def _session_for(*jobs):
    session = MagicMock()
    by_id = {job.job_id: job for job in jobs}
    session.get.side_effect = lambda model, ident: (
        by_id.get(ident) if model is ExecutionJob else None
    )
    return session


def test_context_run_candidate_becomes_a_locked_assignment():
    job = _context_job()
    context = add_proposal_locks(
        _session_for(job), _block_a_context(), _make_problem(), [job.job_id]
    )
    assert context.locked_assignments == (
        LockedAssignment(
            class_id=CLASS_B1_ID,
            faculty_id=FACULTY_2_ID,
            time_slot_id=TIME_SLOT_1_ID,
            room_code="LB 102",
            meeting_days="TTh",
            block_id=BLOCK_B_ID,
            offering_id=OFFERING_B1_ID,
            units=3,
        ),
    )


def test_edited_context_run_is_held_as_edited():
    # The proposal was moved by hand in review: holding its original
    # candidate would leave LB 101 on MW looking free to the next Block.
    job = _context_job()
    edited = {
        "class_id": str(CLASS_B1_ID),
        "faculty_id": str(FACULTY_1_ID),
        "room_code": "LB 101",
        "meeting_days": "MW",
        "time_slot_id": str(TIME_SLOT_2_ID),
    }
    context = add_proposal_locks(
        _session_for(job),
        _block_a_context(),
        _make_problem(),
        [job.job_id],
        {job.job_id: [edited]},
    )
    assert context.locked_assignments == (
        LockedAssignment(
            class_id=CLASS_B1_ID,
            faculty_id=FACULTY_1_ID,
            time_slot_id=TIME_SLOT_2_ID,
            room_code="LB 101",
            meeting_days="MW",
            block_id=BLOCK_B_ID,
            offering_id=OFFERING_B1_ID,
            units=3,
        ),
    )


def test_context_edits_must_name_listed_runs():
    from pydantic import ValidationError

    from app.schemas.jobs import SubmitGAJobRequest

    with pytest.raises(ValidationError, match="context_job_ids"):
        SubmitGAJobRequest(
            term_id=uuid4(),
            block_id=uuid4(),
            base_schedule_id=uuid4(),
            context_job_ids=[],
            context_edits=[{"job_id": str(uuid4()), "assignments": []}],
        )


def test_accepted_assignment_wins_over_a_context_run():
    job = _context_job()
    accepted = LockedAssignment(
        class_id=CLASS_B1_ID,
        faculty_id=FACULTY_1_ID,
        time_slot_id=TIME_SLOT_2_ID,
        room_code="LB 101",
        meeting_days="MW",
        block_id=BLOCK_B_ID,
        offering_id=OFFERING_B1_ID,
        units=3,
    )
    base = replace(_block_a_context(), locked_assignments=(accepted,))
    context = add_proposal_locks(_session_for(job), base, _make_problem(), [job.job_id])
    assert context.locked_assignments == (accepted,)


def test_no_context_runs_leaves_the_context_unchanged():
    base = _block_a_context()
    assert add_proposal_locks(MagicMock(), base, _make_problem(), []) is base


@pytest.mark.parametrize(
    "job, message",
    [
        (_context_job(status=JobStatus.RUNNING.value), "not a completed"),
        (_context_job(block_id=BLOCK_A_ID), "another Block"),
        (_context_job(term_id=uuid4()), "another Block"),
        (_context_job(base_schedule_id=uuid4()), "another Block"),
    ],
)
def test_unusable_context_run_is_refused(job, message):
    with pytest.raises(GAContextJobError) as caught:
        add_proposal_locks(
            _session_for(job), _block_a_context(), _make_problem(), [job.job_id]
        )
    assert message in str(caught.value)
    assert caught.value.job_id == job.job_id


def test_missing_context_run_is_refused():
    with pytest.raises(GAContextJobError):
        add_proposal_locks(_session_for(), _block_a_context(), _make_problem(), [uuid4()])


def test_context_job_ids_require_a_block_and_are_unique():
    job_id = uuid4()
    with pytest.raises(ValidationError):
        SubmitGAJobRequest(term_id=TERM_ID, context_job_ids=[job_id])
    with pytest.raises(ValidationError):
        SubmitGAJobRequest(
            term_id=TERM_ID,
            block_id=BLOCK_A_ID,
            base_schedule_id=uuid4(),
            context_job_ids=[job_id, job_id],
        )
