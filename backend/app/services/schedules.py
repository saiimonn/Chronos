import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, undefer

from app.core.config import settings
from app.core.errors import APIError
from app.domain import (
    Assignment,
    CandidateSchedule,
    ConflictCode,
    SchedulingProblem,
)
from app.domain.calendar import DCISM_CALENDAR
from app.domain.meeting_patterns import meeting_label, stretched_end
from app.models import (
    BlockSection,
    BlockSectionOffering,
    ExecutionJob,
    Faculty,
    Room,
    Schedule,
    ScheduleRecord,
    ScheduleType,
    ScheduleUnlockedBlock,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)
from app.models.execution_job import JobStatus, JobType
from app.repositories.protocols import SchedulingProblemNotFoundError
from app.repositories.jobs import JobRepository
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.schemas.schedule import (
    AssignmentDiff,
    AssignmentUpdate,
    BlockReplacementConfirmRequest,
    BlockReplacementPreviewRequest,
    BlockReplacementPreviewResponse,
    ReplacementAssignmentDetail,
    ReplacementAssignmentPayload,
)
from app.services.schedule_export import (
    CSV_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    build_csv,
    build_pdf,
    build_xlsx,
)
from app.services.audit import record_event
from app.services.jobs.codecs import decode_block_generation_context
from app.services.validation.engine import (
    ConstraintEvaluationError,
    StaleFacultyMaskError,
)
from app.services.validation.validator import ScheduleValidator


# Every read of a schedule serializes Block and Offering provenance, so the
# link row is loaded with the records instead of one query per assignment.
_RECORDS_WITH_PROVENANCE = selectinload(Schedule.records).selectinload(
    ScheduleRecord.block_class
)


EXPORT_HEADERS = (
    "academic_year",
    "term_number",
    "subject_code",
    "subject_title",
    "group_label",
    "block_name",
    "program_code",
    "year_level",
    "section_letter",
    "capacity",
    "faculty_name",
    "room_code",
    "meeting_days",
    "time_slot",
)

_MEETING_DAY_TOKENS = ("M", "T", "W", "Th", "F", "Sa", "Su")
_MEETING_DAY_RANK = {token: rank for rank, token in enumerate(_MEETING_DAY_TOKENS)}


def _share_lock_faculty(db: Session) -> None:
    """Hold faculty rows until commit, as job submission does.

    A deactivation then waits for this write, so an is_active read here stays
    true until the assignment it allowed is committed.
    """
    db.scalars(
        select(Faculty.faculty_id).order_by(Faculty.faculty_id).with_for_update(read=True)
    ).all()


def _refuse_new_inactive_faculty(
    problem: SchedulingProblem,
    proposals: Iterable[tuple[UUID, UUID]],
    current_records: Iterable[ScheduleRecord],
) -> None:
    """Refuse giving a past (inactive) teacher a class they do not already teach.

    Allowed only where the current schedule already has them on that class, or
    on another Block of the same offering: Blocks of an offering meet in one
    session, so a run pinned to that session must keep its teacher.
    """
    inactive = {f.faculty_id for f in problem.faculty if not f.is_active}
    if not inactive:
        return
    offering_of = {c.class_id: c.offering_id for c in problem.classes}
    held_classes: set[tuple[UUID, UUID]] = set()
    held_sessions: set[tuple[UUID, UUID]] = set()
    for record in current_records:
        if record.class_id is None or record.faculty_id not in inactive:
            continue
        held_classes.add((record.class_id, record.faculty_id))
        if offering_of.get(record.class_id) is not None:
            held_sessions.add((offering_of[record.class_id], record.faculty_id))
    for class_id, faculty_id in proposals:
        if faculty_id not in inactive or (class_id, faculty_id) in held_classes:
            continue
        offering_id = offering_of.get(class_id)
        if offering_id is not None and (offering_id, faculty_id) in held_sessions:
            continue
        raise APIError(
            409,
            "FACULTY_INACTIVE",
            "This faculty member is marked as not currently teaching.",
            {"faculty_id": str(faculty_id)},
        )


def _refuse_new_inactive_rooms(
    problem: SchedulingProblem,
    proposals: Iterable[tuple[UUID, str]],
    current_records: Iterable[ScheduleRecord],
) -> None:
    """Refuse putting a class in a retired (inactive) room it is not already in.

    Mirrors the faculty rule: a placement the current schedule already holds
    keeps validating, so existing schedules stay valid, but nothing new lands
    in a room marked as no longer in use (CHR-240).
    """
    inactive = {r.room_code for r in problem.rooms if not r.is_active}
    if not inactive:
        return
    held = {
        (record.class_id, record.room_code)
        for record in current_records
        if record.class_id is not None and record.room_code in inactive
    }
    for class_id, room_code in proposals:
        if room_code in inactive and (class_id, room_code) not in held:
            raise APIError(
                409,
                "ROOM_INACTIVE",
                "This room is marked as no longer in use.",
                {"room_code": room_code},
            )


def persist_candidate(
    db: Session,
    candidate: CandidateSchedule,
    *,
    claim_token: UUID | None = None,
    result: dict[str, Any] | None = None,
) -> Schedule:
    """Persist a GA result without coupling the worker to ORM models.

    CHR-185: *result* is the job result, committed with the records.
    """
    if candidate.job_id is not None:
        existing = find_published_schedule(db, candidate.job_id, claim_token)
        if existing is not None:
            return existing
        repository = JobRepository(db)
        # The claim lock blocks external heartbeats during publication.
        if repository.heartbeat(
            candidate.job_id, claim_token, lease_seconds=settings.job_lease_seconds,
        ) is None:
            raise ValueError("GA publication requires a live worker claim")

    # Held through the commit below, so a deactivation cannot land between
    # the inactive-teacher check and the published records.
    _share_lock_faculty(db)
    problem = _get_problem(db, candidate.term_id)
    if candidate.schedule_id is not None and db.get(Schedule, candidate.schedule_id):
        raise APIError(
            409,
            "SCHEDULE_EXISTS",
            "A schedule with this identifier already exists.",
        )
    _validate_candidate(problem, candidate)
    # A term-wide run starts from nothing and has no pins, so every placement
    # is new: refuse a teacher deactivated while the run was in flight.
    _refuse_new_inactive_faculty(
        problem, ((a.class_id, a.faculty_id) for a in candidate.assignments), ()
    )

    sections = {section.class_id: section for section in problem.classes}
    slots = {slot.time_slot_id: slot for slot in problem.time_slots}
    schedule_kwargs: dict[str, Any] = {"term_id": candidate.term_id}
    if candidate.schedule_id is not None:
        schedule_kwargs["schedule_id"] = candidate.schedule_id
    if candidate.job_id is not None:
        schedule_kwargs["job_id"] = candidate.job_id
    if result is not None:
        schedule_kwargs["publication_result"] = result
    schedule = Schedule(**schedule_kwargs)
    db.add(schedule)
    db.flush()
    for assignment in candidate.assignments:
        section = sections[assignment.class_id]
        slot = slots[assignment.time_slot_id]
        schedule.records.append(
            ScheduleRecord(
                faculty_id=assignment.faculty_id,
                subject_code=section.subject_code,
                term_id=candidate.term_id,
                class_id=assignment.class_id,
                time_slot_id=assignment.time_slot_id,
                room_code=assignment.room_code,
                meeting_days=assignment.meeting_days,
                time_slot=slot.label,
                is_historical=False,
            )
        )
    db.flush()
    if candidate.job_id is not None and not repository.lock_live_claim(
        candidate.job_id, claim_token, job_type=JobType.GA,
    ):
        raise ValueError("GA publication requires a live worker claim")
    db.commit()
    return get_schedule(db, schedule.schedule_id)


def find_published_schedule(
    db: Session, job_id: UUID, claim_token: UUID | None,
) -> Schedule | None:
    """Return the schedule *job_id* already published, under its live claim.

    Locks the job row, so the caller's transaction holds the claim.
    """
    if claim_token is None or not JobRepository(db).lock_live_claim(
        job_id, claim_token, job_type=JobType.GA,
    ):
        raise ValueError("GA publication requires a live worker claim")
    return db.scalar(
        select(Schedule)
        .options(undefer(Schedule.publication_result))
        .where(Schedule.job_id == job_id)
    )


def list_schedules(
    db: Session,
    term_id: UUID | None = None,
    schedule_type: ScheduleType | None = None,
) -> list[Schedule]:
    statement = select(Schedule).options(_RECORDS_WITH_PROVENANCE)
    if term_id is not None:
        statement = statement.where(Schedule.term_id == term_id)
    if schedule_type is not None:
        statement = statement.where(Schedule.schedule_type == schedule_type.value)
    return list(
        db.scalars(statement.order_by(Schedule.created_at, Schedule.schedule_id)).all()
    )


def get_schedule(
    db: Session, schedule_id: UUID, *, for_update: bool = False
) -> Schedule:
    query = (
        select(Schedule)
        .options(_RECORDS_WITH_PROVENANCE)
        .where(Schedule.schedule_id == schedule_id)
    )
    schedule = db.scalar(query.with_for_update() if for_update else query)
    if schedule is None:
        raise APIError(404, "NOT_FOUND", "The requested schedule was not found.")
    return schedule


def get_timetable_projection(
    db: Session,
    schedule_id: UUID,
    term_id: UUID,
    block_id: UUID | None = None,
    room_code: str | None = None,
    faculty_id: UUID | None = None,
) -> dict[str, Any]:
    term = db.get(Term, term_id)
    if term is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")

    schedule = db.scalar(
        select(Schedule).where(
            Schedule.schedule_id == schedule_id,
            Schedule.term_id == term_id,
        )
    )
    if schedule is None:
        raise APIError(
            404,
            "NOT_FOUND",
            "The requested schedule was not found in the requested term.",
        )
    if not term.is_active:
        raise APIError(
            409,
            "TERM_NOT_ACTIVE",
            "Committee timetable projections are limited to the active term.",
        )

    if block_id is not None and db.scalar(
        select(BlockSection.block_id).where(
            BlockSection.block_id == block_id,
            BlockSection.term_id == term_id,
        )
    ) is None:
        raise APIError(
            404, "NOT_FOUND", "The requested Block Section was not found."
        )
    if room_code is not None and db.get(Room, room_code) is None:
        raise APIError(404, "NOT_FOUND", "The requested room was not found.")
    if faculty_id is not None and db.get(Faculty, faculty_id) is None:
        raise APIError(404, "NOT_FOUND", "The requested faculty was not found.")

    statement = (
        select(
            ScheduleRecord.record_id,
            ScheduleRecord.faculty_id,
            ScheduleRecord.room_code,
            ScheduleRecord.meeting_days,
            ScheduleRecord.time_slot_id,
            BlockSectionOffering.offering_id,
            BlockSection.block_id,
            BlockSection.program_code,
            BlockSection.year_level,
            BlockSection.section_letter,
            Subject.subject_code,
            Subject.subject_title,
            SubjectOffering.group_no,
            Faculty.full_name.label("faculty_name"),
            Room.room_type,
            Room.capacity.label("room_capacity"),
            TimeSlot.label.label("time_slot_label"),
            TimeSlot.start_time,
            TimeSlot.end_time,
        )
        .select_from(ScheduleRecord)
        .join(
            BlockSectionOffering,
            BlockSectionOffering.class_id == ScheduleRecord.class_id,
        )
        .join(BlockSection, BlockSection.block_id == BlockSectionOffering.block_id)
        .join(
            SubjectOffering,
            SubjectOffering.offering_id == BlockSectionOffering.offering_id,
        )
        .join(Subject, Subject.subject_code == ScheduleRecord.subject_code)
        .join(Faculty, Faculty.faculty_id == ScheduleRecord.faculty_id)
        .outerjoin(Room, Room.room_code == ScheduleRecord.room_code)
        .join(TimeSlot, TimeSlot.time_slot_id == ScheduleRecord.time_slot_id)
        .where(ScheduleRecord.schedule_id == schedule_id)
    )
    if block_id is not None:
        statement = statement.where(BlockSection.block_id == block_id)
    if room_code is not None:
        statement = statement.where(ScheduleRecord.room_code == room_code)
    if faculty_id is not None:
        statement = statement.where(ScheduleRecord.faculty_id == faculty_id)

    rows = sorted(db.execute(statement).mappings().all(), key=_timetable_sort_key)
    return {
        "schedule_id": schedule.schedule_id,
        "term_id": schedule.term_id,
        "schedule_type": schedule.schedule_type,
        "created_at": schedule.created_at,
        "finalized_at": schedule.finalized_at,
        "finalization_state": (
            "finalized" if schedule.finalized_at is not None else "candidate"
        ),
        "calendar": {
            "teaching_days": [day.value for day in DCISM_CALENDAR.teaching_days],
            "opening_time": DCISM_CALENDAR.opening_time,
            "closing_time": DCISM_CALENDAR.closing_time,
            "interval_minutes": int(DCISM_CALENDAR.interval.total_seconds() // 60),
        },
        "assignments": [
            {
                "record_id": row["record_id"],
                "offering_id": row["offering_id"],
                "subject_code": row["subject_code"],
                "subject_title": row["subject_title"],
                "group_label": f'{row["subject_code"]} G{row["group_no"]}',
                "block_id": row["block_id"],
                "block_name": (
                    f'{row["program_code"]}-{row["year_level"]}'
                    f'{row["section_letter"]}'
                ).upper(),
                "program_code": row["program_code"],
                "year_level": row["year_level"],
                "faculty_id": row["faculty_id"],
                "faculty_name": row["faculty_name"],
                "room_code": row["room_code"],
                "room_type": row["room_type"],
                "room_capacity": row["room_capacity"],
                "meeting_days": row["meeting_days"],
                "time_slot_id": row["time_slot_id"],
                "time_slot_label": row["time_slot_label"],
                "start_time": row["start_time"],
                # A Friday-only class holds two sessions back to back.
                "end_time": stretched_end(
                    row["start_time"], row["end_time"], row["meeting_days"]
                ),
            }
            for row in rows
        ],
    }


def _meeting_days_sort_key(meeting_days: str | None) -> tuple[int, ...]:
    if not meeting_days:
        return (len(_MEETING_DAY_TOKENS),)

    ranks: list[int] = []
    cursor = 0
    while cursor < len(meeting_days):
        token = next(
            (
                candidate
                for candidate in ("Th", "Sa", "Su", "M", "T", "W", "F")
                if meeting_days.startswith(candidate, cursor)
            ),
            None,
        )
        if token is None:
            ranks.append(len(_MEETING_DAY_TOKENS))
            cursor += 1
        else:
            ranks.append(_MEETING_DAY_RANK[token])
            cursor += len(token)
    return tuple(ranks)


def _timetable_sort_key(row) -> tuple:
    day_key = _meeting_days_sort_key(row["meeting_days"])
    return (
        day_key[0],
        row["start_time"],
        day_key,
        row["program_code"],
        row["year_level"],
        row["section_letter"],
        str(row["block_id"]),
        row["subject_code"],
        row["group_no"],
        str(row["offering_id"]),
        row["room_code"] or "",
        str(row["record_id"]),
    )


def create_master_schedule(
    db: Session, term_id: UUID, actor_id: UUID | None = None
) -> Schedule:
    if db.get(Term, term_id) is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    existing = db.scalar(
        select(Schedule.schedule_id).where(
            Schedule.term_id == term_id,
            Schedule.schedule_type == ScheduleType.MASTER.value,
        )
    )
    if existing is not None:
        raise APIError(
            409,
            "MASTER_SCHEDULE_EXISTS",
            "A master schedule already exists for this term.",
        )
    schedule = Schedule(term_id=term_id, schedule_type=ScheduleType.MASTER.value)
    db.add(schedule)
    try:
        db.flush()
        record_event(
            db,
            actor_id=actor_id,
            operation="schedule.master_create",
            target_type="schedule",
            target_id=schedule.schedule_id,
            details={"term_id": term_id},
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        constraint_name = getattr(
            getattr(error.orig, "diag", None), "constraint_name", None
        )
        if constraint_name != "uq_schedule_one_master_per_term":
            raise
        raise APIError(
            409,
            "MASTER_SCHEDULE_EXISTS",
            "A master schedule already exists for this term.",
        ) from error
    return get_schedule(db, schedule.schedule_id)


def get_master_schedule(db: Session, term_id: UUID) -> Schedule:
    schedule = db.scalar(
        select(Schedule)
        .options(_RECORDS_WITH_PROVENANCE)
        .where(
            Schedule.term_id == term_id,
            Schedule.schedule_type == ScheduleType.MASTER.value,
        )
    )
    if schedule is None:
        raise APIError(404, "NOT_FOUND", "No master schedule exists for this term.")
    return schedule


def validate_schedule(db: Session, schedule_id: UUID):
    schedule = get_schedule(db, schedule_id)
    problem = _get_problem(db, schedule.term_id)
    return _validate_candidate(
        problem,
        _to_candidate(schedule),
        flag_inactive_faculty=schedule.finalized_at is None,
    )


def update_assignment(
    db: Session,
    schedule_id: UUID,
    assignment_id: UUID,
    payload: AssignmentUpdate,
    actor_id: UUID | None = None,
) -> tuple[Schedule, Any]:
    # An Offering-wide edit writes only the peers that differ from what this
    # request read, so concurrent edits must not read the same records.
    schedule = get_schedule(db, schedule_id, for_update=True)
    _require_editable(schedule)
    record = next(
        (item for item in schedule.records if item.record_id == assignment_id), None
    )
    if record is None:
        raise APIError(
            404, "NOT_FOUND", "The requested schedule assignment was not found."
        )

    if payload.faculty_id is not None:
        _share_lock_faculty(db)
    problem = _get_problem(db, schedule.term_id)
    # Blocks of one offering attend one combined session, so a placement edit
    # moves every Block in it; editing one alone would split the session.
    offering_by_class = {
        section.class_id: section.offering_id for section in problem.classes
    }
    offering_id = offering_by_class.get(record.class_id)
    session_records = [
        item
        for item in schedule.records
        if item is record
        or (
            offering_id is not None
            and offering_by_class.get(item.class_id) == offering_id
        )
    ]
    # Paired Blocks take each LAB at one time and on the same days, so moving
    # one LAB's time or days moves its partner's too (never its teacher or
    # room, which differ by design). Without this every LAB edit would leave
    # a mismatch that only a second edit, in the other Block, could clear.
    lab_partner_records: list[ScheduleRecord] = []
    if payload.time_slot_id is not None or payload.meeting_days is not None:
        partner_ids = {
            problem.lab_partners.get(item.class_id) for item in session_records
        } - {None}
        lab_partner_records = [
            item
            for item in schedule.records
            if item.class_id in partner_ids and item not in session_records
        ]
    _require_blocks_unlocked(
        schedule,
        problem,
        {item.class_id for item in session_records + lab_partner_records},
    )
    # A past teacher may stay where already assigned, but is never newly given
    # a class, including a peer Block the edit writes the teacher onto.
    if payload.faculty_id is not None and any(
        item.faculty_id != payload.faculty_id for item in session_records
    ):
        member = next(
            (f for f in problem.faculty if f.faculty_id == payload.faculty_id), None
        )
        if member is not None and not member.is_active:
            raise APIError(
                409,
                "FACULTY_INACTIVE",
                "This faculty member is marked as not currently teaching.",
                {"faculty_id": str(payload.faculty_id)},
            )
    # Likewise a retired room may keep the sessions it already holds, but is
    # never newly given one.
    if payload.room_code is not None and any(
        item.room_code != payload.room_code for item in session_records
    ):
        room = next(
            (r for r in problem.rooms if r.room_code == payload.room_code), None
        )
        if room is not None and not room.is_active:
            raise APIError(
                409,
                "ROOM_INACTIVE",
                "This room is marked as no longer in use.",
                {"room_code": payload.room_code},
            )
    fields = ("faculty_id", "room_code", "meeting_days", "time_slot_id", "time_slot")
    savepoint = db.begin_nested()
    try:
        originals = [
            (item, {field: getattr(item, field) for field in fields})
            for item in session_records + lab_partner_records
        ]
        for field, value in payload.model_dump(exclude_unset=True).items():
            if value is not None:
                for item in session_records:
                    setattr(item, field, value)
                if field in ("time_slot_id", "meeting_days"):
                    for item in lab_partner_records:
                        setattr(item, field, value)

        result = _validate_candidate(problem, _to_candidate(schedule))
        _require_teaching_days(
            result,
            {item.class_id for item in session_records + lab_partner_records},
        )
        unavailable = next(
            (
                item
                for conflict in result.constraint_report.conflicts
                if conflict.code == ConflictCode.FACULTY_UNAVAILABLE
                for item in session_records
                if item.class_id in conflict.class_ids
            ),
            None,
        )
        if unavailable is not None:
            raise APIError(
                409,
                "FACULTY_UNAVAILABLE",
                "The faculty member is unavailable for the selected time slot.",
                {
                    "faculty_id": str(unavailable.faculty_id),
                    "time_slot_id": str(unavailable.time_slot_id),
                },
            )
        slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        for item in session_records + lab_partner_records:
            item.time_slot = slots[item.time_slot_id].label
        record_event(
            db,
            actor_id=actor_id,
            operation="schedule.assignment_update",
            target_type="schedule",
            target_id=schedule_id,
            details={
                "assignment_id": assignment_id,
                "changes": [
                    {
                        "record_id": item.record_id,
                        "before": original,
                        "after": {field: getattr(item, field) for field in fields},
                    }
                    for item, original in originals
                ],
            },
        )
        savepoint.commit()
    except (APIError, ConstraintEvaluationError):
        savepoint.rollback()
        for item, original in originals:
            for field, value in original.items():
                setattr(item, field, value)
        raise

    db.commit()
    return get_schedule(db, schedule_id), result


def finalize_schedule(
    db: Session, schedule_id: UUID, actor_id: UUID | None = None
) -> tuple[Schedule, Any]:
    # Hold the edit lock through commit, so no edit lands between validating
    # the records and freezing them.
    schedule = get_schedule(db, schedule_id, for_update=True)
    if schedule.finalized_at is None and schedule.unlocked_blocks:
        problem = _get_problem(db, schedule.term_id)
        names = _block_names(problem, {row.block_id for row in schedule.unlocked_blocks})
        raise APIError(
            409,
            "BLOCKS_UNLOCKED",
            f"Lock every Block before finalizing. Still unlocked: {', '.join(names)}.",
            {"block_ids": sorted(str(row.block_id) for row in schedule.unlocked_blocks)},
        )
    result = validate_schedule(db, schedule_id)
    _require_export_eligible(result)
    already_finalized = schedule.finalized_at is not None
    if not already_finalized:
        schedule.finalized_at = datetime.now(timezone.utc)
    record_event(
        db,
        actor_id=actor_id,
        operation="schedule.finalize",
        target_type="schedule",
        target_id=schedule_id,
        details={
            "already_finalized": already_finalized,
            "finalized_at": schedule.finalized_at,
            "assignment_count": len(schedule.records),
        },
    )
    db.commit()
    return get_schedule(db, schedule_id), result


def delete_schedule(
    db: Session,
    schedule_id: UUID,
    actor_id: UUID | None = None,
    allow_finalized: bool = False,
) -> None:
    """CHR-202: delete a schedule with its records, atomically.

    Only a Department Administrator (allow_finalized) may delete a finalized one.

    Records cascade in the database. Nothing else references a schedule by
    foreign key: historical snapshots keep a provenance id only, and a stale
    replacement preview token fails with 404 once the row is gone.
    """
    schedule = get_schedule(db, schedule_id, for_update=True)
    if not allow_finalized:
        _require_editable(schedule)
    if any(record.is_historical for record in schedule.records):
        raise APIError(
            409,
            "SCHEDULE_HISTORICAL",
            "A schedule holding historical records cannot be deleted.",
        )
    # A queued or running GA job could be reclaimed and republish the schedule.
    if schedule.job_id is not None:
        job = db.get(ExecutionJob, schedule.job_id)
        if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise APIError(
                409,
                "SCHEDULE_JOB_ACTIVE",
                "The job that produced this schedule is still active.",
            )
    record_event(
        db,
        actor_id=actor_id,
        operation="schedule.delete",
        target_type="schedule",
        target_id=schedule_id,
        details={
            "term_id": schedule.term_id,
            "schedule_type": schedule.schedule_type,
            "assignment_count": len(schedule.records),
            "finalized_at": schedule.finalized_at,
            # Enough to reconstruct a deleted schedule (bounded by one term).
            "assignments": [
                {
                    "class_id": record.class_id,
                    "faculty_id": record.faculty_id,
                    "room_code": record.room_code,
                    "meeting_days": record.meeting_days,
                    "time_slot_id": record.time_slot_id,
                }
                for record in schedule.records
            ],
        },
    )
    db.delete(schedule)
    db.commit()


def export_schedule(
    db: Session,
    schedule_id: UUID,
    export_format: str,
    block_id: UUID | None = None,
    room_code: str | None = None,
    faculty_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> tuple[str, str, bytes]:
    schedule = get_schedule(db, schedule_id)
    if schedule.finalized_at is None:
        raise APIError(
            409,
            "SCHEDULE_NOT_FINALIZED",
            "The schedule must be finalized before export.",
        )

    # Never trust finalization or an earlier GA score as current proof of safety.
    problem = _get_problem(db, schedule.term_id)
    result = _validate_candidate(
        problem, _to_candidate(schedule), flag_inactive_faculty=False
    )
    _require_export_eligible(result)

    # Filters narrow the file to what the timetable shows; eligibility above is
    # still decided on the whole schedule.
    # chronos_<school year>_<semester>_<generation>; generation is when the
    # schedule was created, in DCISM local time.
    generated = schedule.created_at.astimezone(ZoneInfo("Asia/Manila"))
    stem = (
        f"chronos_{_filename_part(problem.term.academic_year)}"
        f"_term{problem.term.term_number}_{generated:%Y%m%d-%H%M}"
    )
    records = schedule.records
    scope = ["All blocks", "all rooms"]
    if block_id is not None:
        block = db.get(BlockSection, block_id)
        if block is None or block.term_id != schedule.term_id:
            raise APIError(
                404, "NOT_FOUND", "The requested Block Section was not found."
            )
        sections = {section.class_id: section for section in problem.classes}
        records = [r for r in records if sections[r.class_id].block_id == block_id]
        stem += f"_block_{_filename_part(block.name)}"
        scope[0] = f"Block {block.name}"
    if room_code is not None:
        if db.get(Room, room_code) is None:
            raise APIError(404, "NOT_FOUND", "The requested room was not found.")
        records = [r for r in records if r.room_code == room_code]
        stem += f"_room_{_filename_part(room_code)}"
        scope[1] = f"room {room_code}"
    if faculty_id is not None:
        faculty = db.get(Faculty, faculty_id)
        if faculty is None:
            raise APIError(404, "NOT_FOUND", "The requested faculty was not found.")
        records = [r for r in records if r.faculty_id == faculty_id]
        stem += f"_faculty_{_filename_part(faculty.full_name)}"
        scope.append(f"faculty {faculty.full_name}")

    rows = _export_rows(problem, records)
    if export_format == "csv":
        exported = (f"{stem}.csv", CSV_MEDIA_TYPE, build_csv(EXPORT_HEADERS, rows))
    elif export_format == "pdf":
        metadata = (
            ("Term", f"{problem.term.academic_year} Term {problem.term.term_number}"),
            ("Schedule", f"{schedule.schedule_id} ({schedule.schedule_type})"),
            ("Finalized at", schedule.finalized_at.isoformat()),
            ("Scope", "; ".join(scope)),
            ("Assignments", str(len(rows))),
            ("Generated at", datetime.now(timezone.utc).isoformat()),
        )
        # academic_year and term_number are the "Term" line, not table columns.
        content = build_pdf(EXPORT_HEADERS[2:], [row[2:] for row in rows], metadata)
        exported = (f"{stem}.pdf", PDF_MEDIA_TYPE, content)
    else:
        exported = (f"{stem}.xlsx", XLSX_MEDIA_TYPE, build_xlsx(EXPORT_HEADERS, rows))
    record_event(
        db,
        actor_id=actor_id,
        operation="schedule.export",
        target_type="schedule",
        target_id=schedule_id,
        details={
            "format": export_format,
            "block_id": block_id,
            "room_code": room_code,
            "faculty_id": faculty_id,
            "row_count": len(rows),
            "filename": exported[0],
        },
    )
    db.commit()
    return exported


def serialize_schedule(schedule: Schedule) -> dict[str, Any]:
    return {
        "schedule_id": schedule.schedule_id,
        "term_id": schedule.term_id,
        "schedule_type": schedule.schedule_type,
        "created_at": schedule.created_at,
        "finalized_at": schedule.finalized_at,
        "unlocked_block_ids": sorted(
            (row.block_id for row in schedule.unlocked_blocks), key=str
        ),
        "assignments": [
            {
                "record_id": record.record_id,
                "class_id": record.class_id,
                # The link row is what pins an assignment to one Block taking
                # one Offering, so both identifiers are read back from it
                # rather than re-derived.
                "offering_id": record.block_class.offering_id,
                "block_id": record.block_class.block_id,
                "faculty_id": record.faculty_id,
                "subject_code": record.subject_code,
                "room_code": record.room_code,
                "meeting_days": record.meeting_days,
                "time_slot_id": record.time_slot_id,
                "time_slot": record.time_slot,
            }
            for record in sorted(
                schedule.records,
                key=lambda item: (str(item.class_id), str(item.record_id)),
            )
        ],
    }


def serialize_validation(result) -> dict[str, Any]:
    required = result.required_class_count
    percentage = (result.assigned_class_count / required * 100) if required else 0.0
    return {
        "required_class_count": required,
        "assigned_class_count": result.assigned_class_count,
        "completeness_percentage": percentage,
        "unassigned_class_ids": [str(sid) for sid in result.unassigned_class_ids],
        "hard_conflict_count": result.hard_conflict_count,
        "conflicts": [
            {
                "code": conflict.code.value,
                "resource_key": list(conflict.resource_key),
                "class_ids": [str(sid) for sid in conflict.class_ids],
                "time_slot_ids": [str(tid) for tid in conflict.time_slot_ids],
                "message": conflict.message,
            }
            for conflict in result.constraint_report.conflicts
        ],
        "export_allowed": result.is_export_eligible,
    }


def _get_problem(db: Session, term_id: UUID) -> SchedulingProblem:
    try:
        return SQLAlchemySchedulingProblemProvider(db).get_problem(term_id)
    except SchedulingProblemNotFoundError as error:
        raise APIError(404, "NOT_FOUND", "The schedule term was not found.") from error


def _validate_candidate(
    problem: SchedulingProblem,
    candidate: CandidateSchedule,
    *,
    flag_inactive_faculty: bool = True,
):
    # Schedules still being built report classes of a deactivated teacher, so
    # a roster change shows up at once; a finalized one keeps its past teachers.
    try:
        return ScheduleValidator().validate(
            problem,
            candidate,
            require_allowed_patterns=True,
            flag_inactive_faculty=flag_inactive_faculty,
            flag_long_block_runs=True,
        )
    except StaleFacultyMaskError as error:
        # Its message names the member; the generic error below would not.
        raise APIError(
            409,
            "FACULTY_MASK_STALE",
            str(error),
            {"faculty_id": str(error.faculty_id)},
        ) from error
    except ConstraintEvaluationError as error:
        raise APIError(
            409,
            "INVALID_SCHEDULE_DATA",
            "The schedule contains invalid assignment data.",
            {"reason": str(error)},
        ) from error


def _require_teaching_days(result, class_ids: set[UUID]) -> None:
    """CHR-168: refuse a write that leaves its own classes on a non-teaching day,
    or on a day pattern their term does not allow.

    Other classes keep their conflicts, so each can still be fixed in turn.
    """
    conflict = next(
        (
            conflict
            for conflict in result.constraint_report.conflicts
            if conflict.code
            in (
                ConflictCode.OUTSIDE_TEACHING_CALENDAR,
                ConflictCode.INVALID_MEETING_PATTERN,
            )
            and any(class_id in class_ids for class_id in conflict.class_ids)
        ),
        None,
    )
    if conflict is not None:
        raise APIError(
            409,
            "INVALID_SCHEDULE_DATA",
            "The schedule contains invalid assignment data.",
            {"reason": conflict.message},
        )


def _to_candidate(schedule: Schedule) -> CandidateSchedule:
    from app.domain import Assignment

    return CandidateSchedule(
        schedule_id=schedule.schedule_id,
        term_id=schedule.term_id,
        assignments=tuple(
            Assignment(
                class_id=record.class_id,
                faculty_id=record.faculty_id,
                time_slot_id=record.time_slot_id,
                room_code=record.room_code,
                meeting_days=record.meeting_days,
            )
            for record in schedule.records
            if record.class_id is not None and record.time_slot_id is not None
        ),
    )


def _audit_assignment(item: Any) -> dict[str, Any]:
    return {
        field: getattr(item, field)
        for field in ("class_id", "faculty_id", "room_code", "meeting_days", "time_slot_id")
    }


def _block_names(problem: SchedulingProblem, block_ids: Iterable[UUID]) -> list[str]:
    names = {section.block_id: section.block_name for section in problem.classes}
    return sorted(names.get(block_id, str(block_id)) for block_id in set(block_ids))


def _is_locked(schedule: Schedule, block_id: UUID) -> bool:
    """An approved Block of a master schedule is locked unless unlocked.

    A Block with no records has nothing approved, so nothing to lock.
    """
    if schedule.schedule_type != ScheduleType.MASTER.value:
        return False
    if any(row.block_id == block_id for row in schedule.unlocked_blocks):
        return False
    return bool(_block_records(schedule, block_id))


def _require_blocks_unlocked(
    schedule: Schedule, problem: SchedulingProblem, class_ids: Iterable[UUID]
) -> None:
    """Refuse an edit that would write any record of a locked Block.

    An edit can reach past its own Block: a combined LEC session moves every
    Block attending it, and a paired LAB moves its partner. All of them must
    be unlocked, or the locked week would change behind its approval.
    """
    if schedule.schedule_type != ScheduleType.MASTER.value:
        return
    block_of = {section.class_id: section.block_id for section in problem.classes}
    unlocked = {row.block_id for row in schedule.unlocked_blocks}
    locked = {
        block_of[class_id]
        for class_id in class_ids
        if class_id in block_of and block_of[class_id] not in unlocked
    }
    if not locked:
        return
    names = _block_names(problem, locked)
    raise APIError(
        409,
        "BLOCK_LOCKED",
        (
            f"{names[0]} is locked. Unlock it before editing this class."
            if len(names) == 1
            else f"{', '.join(names)} are locked. They share this class's session "
            "or lab time, so unlock each of them before editing it."
        ),
        {"block_ids": sorted(str(block_id) for block_id in locked)},
    )


def _require_master_block(db: Session, schedule: Schedule, block_id: UUID) -> BlockSection:
    if schedule.schedule_type != ScheduleType.MASTER.value:
        raise APIError(
            409,
            "NOT_MASTER_SCHEDULE",
            "Only Blocks of a master schedule are locked and unlocked.",
        )
    _require_editable(schedule)
    block = db.get(BlockSection, block_id)
    if block is None or block.term_id != schedule.term_id:
        raise APIError(
            404,
            "NOT_FOUND",
            "The requested block was not found for this schedule's term.",
        )
    return block


def _block_records(schedule: Schedule, block_id: UUID) -> list[ScheduleRecord]:
    return [
        record
        for record in schedule.records
        if record.block_class is not None and record.block_class.block_id == block_id
    ]


def unlock_block(
    db: Session, schedule_id: UUID, block_id: UUID, actor_id: UUID | None = None
) -> tuple[Schedule, Any]:
    """Open an approved Block's week for editing again."""
    schedule = get_schedule(db, schedule_id, for_update=True)
    block = _require_master_block(db, schedule, block_id)
    if not _block_records(schedule, block_id):
        raise APIError(
            409,
            "BLOCK_NOT_APPROVED",
            f"{block.name} has no approved week to unlock.",
        )
    if _is_locked(schedule, block_id):
        schedule.unlocked_blocks.append(
            ScheduleUnlockedBlock(block_id=block_id, unlocked_by=actor_id)
        )
        record_event(
            db,
            actor_id=actor_id,
            operation="schedule.block_unlock",
            target_type="schedule",
            target_id=schedule_id,
            details={"block_id": block_id},
        )
    db.commit()
    return get_schedule(db, schedule_id), validate_schedule(db, schedule_id)


def _conflict_details(conflicts: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "code": str(conflict.code),
            "resource_key": list(conflict.resource_key),
            "class_ids": [str(cid) for cid in conflict.class_ids],
            "time_slot_ids": [str(tid) for tid in conflict.time_slot_ids],
            "message": conflict.message,
        }
        for conflict in conflicts
    ]


def lock_block(
    db: Session, schedule_id: UUID, block_id: UUID, actor_id: UUID | None = None
) -> tuple[Schedule, Any]:
    """Lock an unlocked Block again once every class of it is placed cleanly."""
    schedule = get_schedule(db, schedule_id, for_update=True)
    block = _require_master_block(db, schedule, block_id)
    row = next((item for item in schedule.unlocked_blocks if item.block_id == block_id), None)
    result = validate_schedule(db, schedule_id)
    if row is None:
        db.commit()
        return schedule, result
    problem = _get_problem(db, schedule.term_id)
    class_ids = {section.class_id for section in problem.classes if section.block_id == block_id}
    unplaced = [class_id for class_id in result.unassigned_class_ids if class_id in class_ids]
    if unplaced:
        raise APIError(
            409,
            "BLOCK_INCOMPLETE",
            f"{block.name} still has {len(unplaced)} unplaced "
            f"class{'es' if len(unplaced) != 1 else ''}. Place every class before locking it.",
            {"class_ids": [str(class_id) for class_id in unplaced]},
        )
    conflicts = [
        conflict
        for conflict in result.constraint_report.conflicts
        if any(class_id in class_ids for class_id in conflict.class_ids)
    ]
    if conflicts:
        raise APIError(
            409,
            "BLOCK_HAS_CONFLICTS",
            f"{block.name} still has {len(conflicts)} "
            f"conflict{'s' if len(conflicts) != 1 else ''}. Fix them before locking it. "
            f"{conflicts[0].message}",
            {"conflicts": _conflict_details(conflicts)},
        )
    schedule.unlocked_blocks.remove(row)
    record_event(
        db,
        actor_id=actor_id,
        operation="schedule.block_lock",
        target_type="schedule",
        target_id=schedule_id,
        details={"block_id": block_id},
    )
    db.commit()
    return get_schedule(db, schedule_id), result


def clear_block(
    db: Session, schedule_id: UUID, block_id: UUID, actor_id: UUID | None = None
) -> tuple[Schedule, Any]:
    """Withdraw an unlocked Block's approved week so it can be generated afresh.

    Regenerating one Block of a pair is held to whatever its partner already
    has approved (they share LEC sessions and LAB times). Clearing both lets
    the pair be generated again together instead of around the old week.
    """
    schedule = get_schedule(db, schedule_id, for_update=True)
    block = _require_master_block(db, schedule, block_id)
    if _is_locked(schedule, block_id):
        raise APIError(
            409,
            "BLOCK_LOCKED",
            f"{block.name} is locked. Unlock it before clearing its week.",
            {"block_ids": [str(block_id)]},
        )
    records = _block_records(schedule, block_id)
    removed = [_audit_assignment(record) for record in records]
    for record in records:
        db.delete(record)
    for row in list(schedule.unlocked_blocks):
        if row.block_id == block_id:
            schedule.unlocked_blocks.remove(row)
    if removed:
        record_event(
            db,
            actor_id=actor_id,
            operation="schedule.block_clear",
            target_type="schedule",
            target_id=schedule_id,
            details={"block_id": block_id, "before": removed},
        )
    db.commit()
    db.expire(schedule, ["records"])
    return get_schedule(db, schedule_id), validate_schedule(db, schedule_id)


def _require_editable(schedule: Schedule) -> None:
    if schedule.finalized_at is not None:
        raise APIError(
            409,
            "SCHEDULE_FINALIZED",
            "A finalized schedule cannot be modified.",
        )


def _require_export_eligible(result) -> None:
    if result.is_export_eligible:
        return
    raise APIError(
        409,
        "SCHEDULE_NOT_EXPORTABLE",
        "The schedule must be complete and have zero hard conflicts.",
        serialize_validation(result),
    )


def _filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unnamed"


def _export_rows(
    problem: SchedulingProblem, records: Sequence[ScheduleRecord]
) -> list[tuple[Any, ...]]:
    sections = {section.class_id: section for section in problem.classes}
    subjects = {subject.subject_code: subject for subject in problem.subjects}
    faculty = {item.faculty_id: item for item in problem.faculty}
    slots = {slot.time_slot_id: slot for slot in problem.time_slots}
    ordered = sorted(
        records,
        key=lambda record: sections[record.class_id].stable_key,
    )
    return [
        (
            problem.term.academic_year,
            problem.term.term_number,
            record.subject_code,
            subjects[record.subject_code].subject_title,
            sections[record.class_id].group_label,
            sections[record.class_id].block_name,
            sections[record.class_id].program_code,
            sections[record.class_id].year_level,
            sections[record.class_id].section_letter,
            sections[record.class_id].capacity,
            faculty[record.faculty_id].full_name,
            record.room_code,
            record.meeting_days,
            meeting_label(slots[record.time_slot_id], record.meeting_days),
        )
        for record in ordered
    ]


_PREVIEW_TOKEN_TTL_SECONDS = 1800  # 30 minutes


def _get_signing_key() -> bytes:
    return hashlib.sha256(settings.database_url.encode("utf-8")).digest()


def _compute_schedule_hash(schedule: Schedule) -> str:
    """Deterministic hash of the schedule's current assignment rows."""
    items = sorted(
        (
            str(r.class_id),
            str(r.faculty_id),
            r.room_code or "",
            r.meeting_days or "",
            str(r.time_slot_id),
        )
        for r in schedule.records
        if r.class_id is not None
    )
    raw = json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_term_inputs_hash(problem: SchedulingProblem) -> str:
    """Deterministic hash of the term's scheduling problem inputs."""
    classes_data = sorted(
        (
            str(c.class_id),
            str(c.block_id),
            str(c.offering_id),
            c.subject_code,
            c.capacity,
        )
        for c in problem.classes
    )
    faculty_data = sorted(
        # is_active included: deactivating someone after a preview must
        # invalidate it, since the GA would no longer offer them.
        # Teaching restrictions only when set, so an unrestricted member
        # hashes as before and existing previews stay current.
        (str(f.faculty_id), f.is_full_time, f.availability_mask, f.is_active)
        + (
            (f.lecture_only, tuple(sorted(f.non_preferred_subjects)))
            if f.lecture_only or f.non_preferred_subjects
            else ()
        )
        + ((f.day_availability_masks,) if f.day_availability_masks else ())
        for f in problem.faculty
    )
    # is_active included, as for faculty: retiring a room or subject after a
    # preview changes what the GA may use, so the preview must go stale.
    rooms_data = sorted(
        (r.room_code, r.capacity, r.room_type.value, r.expanded, r.is_active)
        for r in problem.rooms
    )
    subjects_data = sorted(
        (s.subject_code, s.units, s.subject_type.value, s.is_active)
        for s in problem.subjects
    )
    slots_data = sorted(
        (str(t.time_slot_id), t.label, str(t.start_time), str(t.end_time))
        for t in problem.time_slots
    )
    offering_blocks_data = sorted(
        (str(k), tuple(str(b) for b in v))
        for k, v in problem.offering_block_ids.items()
    )
    data = {
        "classes": classes_data,
        "faculty": faculty_data,
        "rooms": rooms_data,
        "subjects": subjects_data,
        "time_slots": slots_data,
        "offering_block_ids": offering_blocks_data,
    }
    # Readiness refuses Blocks of a retired program, so retiring one after a
    # preview must make it stale too. Listed only when some program is
    # retired, so hashes taken while every program is active are unchanged.
    retired_programs = sorted(p.program_code for p in problem.programs if not p.is_active)
    if retired_programs:
        data["retired_programs"] = retired_programs
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_item(obj: Any, key: str) -> Any:
    """Safely get an attribute or dictionary key."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _compute_assignments_hash(assignments: Sequence[Any]) -> str:
    """Deterministic hash of a proposed assignment list."""
    items = sorted(
        (
            str(_get_item(a, "class_id")),
            str(_get_item(a, "faculty_id")),
            _get_item(a, "room_code"),
            _get_item(a, "meeting_days"),
            str(_get_item(a, "time_slot_id")),
        )
        for a in assignments
    )
    raw = json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _create_preview_token(
    schedule_id: UUID,
    block_id: UUID,
    schedule_hash: str,
    term_inputs_hash: str,
    assignments: Sequence[Any],
) -> str:
    issued_at = int(datetime.now(timezone.utc).timestamp())
    assignments_list = [
        {
            "class_id": str(_get_item(a, "class_id")),
            "faculty_id": str(_get_item(a, "faculty_id")),
            "room_code": _get_item(a, "room_code"),
            "meeting_days": _get_item(a, "meeting_days"),
            "time_slot_id": str(_get_item(a, "time_slot_id")),
        }
        for a in assignments
    ]
    payload = {
        "schedule_id": str(schedule_id),
        "block_id": str(block_id),
        "schedule_hash": schedule_hash,
        "term_inputs_hash": term_inputs_hash,
        "assignments_hash": _compute_assignments_hash(assignments),
        "assignments": assignments_list,
        "iat": issued_at,
    }
    payload_bytes = json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    sig = hmac.new(_get_signing_key(), payload_bytes, hashlib.sha256).hexdigest()
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("ascii")
    return f"{b64_payload}.{sig}"


def _verify_preview_token(
    token: str,
    expected_schedule_id: UUID,
    expected_block_id: UUID,
) -> dict[str, Any]:
    parts = token.strip().split(".")
    if len(parts) != 2:
        raise APIError(
            400, "INVALID_PREVIEW_TOKEN", "The preview token format is invalid."
        )
    b64_payload, sig = parts
    try:
        payload_bytes = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
        expected_sig = hmac.new(
            _get_signing_key(), payload_bytes, hashlib.sha256
        ).hexdigest()
    except Exception as exc:
        raise APIError(
            400, "INVALID_PREVIEW_TOKEN", "The preview token cannot be decoded."
        ) from exc

    if not hmac.compare_digest(sig, expected_sig):
        raise APIError(
            400, "INVALID_PREVIEW_TOKEN", "The preview token signature is invalid."
        )

    try:
        data = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise APIError(
            400,
            "INVALID_PREVIEW_TOKEN",
            "The preview token payload is invalid JSON.",
        ) from exc

    now = int(datetime.now(timezone.utc).timestamp())
    if now - data.get("iat", 0) > _PREVIEW_TOKEN_TTL_SECONDS:
        raise APIError(
            409, "STALE_PREVIEW", "The replacement preview has expired."
        )

    if data.get("schedule_id") != str(expected_schedule_id):
        raise APIError(
            400,
            "INVALID_PREVIEW_TOKEN",
            "The preview token does not match this schedule.",
        )

    if data.get("block_id") != str(expected_block_id):
        raise APIError(
            400,
            "INVALID_PREVIEW_TOKEN",
            "The preview token does not match this block.",
        )

    return data


def preview_block_replacement(
    db: Session,
    schedule_id: UUID,
    block_id: UUID,
    payload: BlockReplacementPreviewRequest,
) -> dict[str, Any]:
    schedule = get_schedule(db, schedule_id)
    _require_editable(schedule)

    block = db.get(BlockSection, block_id)
    if block is None or block.term_id != schedule.term_id:
        raise APIError(
            404,
            "NOT_FOUND",
            "The requested block was not found for this schedule's term.",
        )

    problem = _get_problem(db, schedule.term_id)

    # 1. Resolve proposed assignments
    proposed_raw: list[ReplacementAssignmentPayload] = []
    if payload.job_id is not None:
        job = db.get(ExecutionJob, payload.job_id)
        if job is None:
            raise APIError(
                404, "NOT_FOUND", "The referenced generation job was not found."
            )
        if job.status not in (JobStatus.SUCCEEDED, JobStatus.SUCCEEDED.value):
            raise APIError(
                409,
                "JOB_NOT_COMPLETED",
                "The generation job has not completed successfully.",
            )
        if job.job_type != JobType.GA:
            raise APIError(
                400,
                "INVALID_JOB",
                "The referenced job is not a GA generation job.",
            )
        block_ctx = (
            job.request.get("block_context") if isinstance(job.request, dict) else None
        )
        if not isinstance(block_ctx, dict) or "context" not in block_ctx:
            raise APIError(
                400,
                "INVALID_JOB",
                "The referenced job is not a per-Block generation job.",
            )

        try:
            job_ctx = decode_block_generation_context(block_ctx)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise APIError(
                400,
                "INVALID_JOB",
                "The referenced job is not a per-Block generation job.",
            ) from None
        if job_ctx.selected_block_id != block_id:
            raise APIError(
                400,
                "INVALID_JOB",
                "The generation job was not run for this block.",
            )
        if job_ctx.term.term_id != schedule.term_id:
            raise APIError(
                400,
                "INVALID_JOB",
                "The generation job was not run for this schedule's term.",
            )
        if (
            job_ctx.base_schedule_id is not None
            and job_ctx.base_schedule_id != schedule_id
        ):
            raise APIError(
                400,
                "INVALID_JOB",
                "The generation job was not run against this schedule.",
            )

        if (
            not job.result
            or "candidate" not in job.result
            or "assignments" not in job.result["candidate"]
        ):
            raise APIError(
                409, "JOB_FAILED", "The generation job has no candidate result."
            )

        proposed_raw = [
            ReplacementAssignmentPayload(
                class_id=UUID(a["class_id"]),
                faculty_id=UUID(a["faculty_id"]),
                room_code=a["room_code"],
                meeting_days=a["meeting_days"],
                time_slot_id=UUID(a["time_slot_id"]),
            )
            for a in job.result["candidate"]["assignments"]
        ]
    else:
        proposed_raw = payload.assignments or []

    # 2. Validate proposed assignments against block and problem catalog
    block_classes = [c for c in problem.classes if c.block_id == block_id]
    block_classes_by_id = {c.class_id: c for c in block_classes}
    seen_classes: set[UUID] = set()

    faculty_ids = {f.faculty_id for f in problem.faculty}
    room_codes = {r.room_code for r in problem.rooms}
    slot_ids = {s.time_slot_id for s in problem.time_slots}
    slots_by_id = {s.time_slot_id: s for s in problem.time_slots}
    # Judged against the schedule as it is now, for GA and hand-written
    # proposals alike: a GA run's own view may be stale.
    _refuse_new_inactive_faculty(
        problem, ((a.class_id, a.faculty_id) for a in proposed_raw), schedule.records
    )
    _refuse_new_inactive_rooms(
        problem, ((a.class_id, a.room_code) for a in proposed_raw), schedule.records
    )

    for a in proposed_raw:
        if a.class_id not in block_classes_by_id:
            raise APIError(
                422,
                "INVALID_ASSIGNMENT",
                f"Class {a.class_id} does not belong to block {block_id}.",
            )
        if a.class_id in seen_classes:
            raise APIError(
                422,
                "DUPLICATE_ASSIGNMENT",
                f"Duplicate assignment for class {a.class_id}.",
            )
        seen_classes.add(a.class_id)

        if a.faculty_id not in faculty_ids:
            raise APIError(
                422,
                "INVALID_ASSIGNMENT",
                f"Faculty {a.faculty_id} was not found.",
            )
        if a.room_code not in room_codes:
            raise APIError(
                422,
                "INVALID_ASSIGNMENT",
                f"Room {a.room_code} was not found.",
            )
        if a.time_slot_id not in slot_ids:
            raise APIError(
                422,
                "INVALID_ASSIGNMENT",
                f"Time slot {a.time_slot_id} was not found.",
            )

    # 3. Build proposed_details
    proposed_details: list[dict[str, Any]] = [
        {
            "class_id": a.class_id,
            "offering_id": block_classes_by_id[a.class_id].offering_id,
            "block_id": block_id,
            "faculty_id": a.faculty_id,
            "subject_code": block_classes_by_id[a.class_id].subject_code,
            "room_code": a.room_code,
            "meeting_days": a.meeting_days,
            "time_slot_id": a.time_slot_id,
            "time_slot": slots_by_id[a.time_slot_id].label,
        }
        for a in sorted(proposed_raw, key=lambda item: str(item.class_id))
    ]

    # 4. Build changed_values diff
    existing_block_records = {
        r.class_id: r
        for r in schedule.records
        if r.class_id in block_classes_by_id
    }
    proposed_by_class = {a.class_id: a for a in proposed_raw}

    changed_values: list[dict[str, Any]] = []
    for b_class in sorted(block_classes, key=lambda c: str(c.class_id)):
        cid = b_class.class_id
        curr_rec = existing_block_records.get(cid)
        prop_item = proposed_by_class.get(cid)

        curr_detail = None
        if curr_rec is not None:
            curr_detail = {
                "class_id": curr_rec.class_id,
                "offering_id": b_class.offering_id,
                "block_id": block_id,
                "faculty_id": curr_rec.faculty_id,
                "subject_code": curr_rec.subject_code,
                "room_code": curr_rec.room_code,
                "meeting_days": curr_rec.meeting_days,
                "time_slot_id": curr_rec.time_slot_id,
                "time_slot": (
                    slots_by_id[curr_rec.time_slot_id].label
                    if curr_rec.time_slot_id in slots_by_id
                    else curr_rec.time_slot or ""
                ),
            }

        prop_detail = None
        if prop_item is not None:
            prop_detail = {
                "class_id": prop_item.class_id,
                "offering_id": b_class.offering_id,
                "block_id": block_id,
                "faculty_id": prop_item.faculty_id,
                "subject_code": b_class.subject_code,
                "room_code": prop_item.room_code,
                "meeting_days": prop_item.meeting_days,
                "time_slot_id": prop_item.time_slot_id,
                "time_slot": slots_by_id[prop_item.time_slot_id].label,
            }

        if curr_rec is None and prop_item is not None:
            status = "added"
            changed_fields = [
                "faculty_id",
                "room_code",
                "meeting_days",
                "time_slot_id",
            ]
        elif curr_rec is not None and prop_item is None:
            status = "removed"
            changed_fields = [
                "faculty_id",
                "room_code",
                "meeting_days",
                "time_slot_id",
            ]
        elif curr_rec is not None and prop_item is not None:
            changed_fields = []
            if curr_rec.faculty_id != prop_item.faculty_id:
                changed_fields.append("faculty_id")
            if curr_rec.room_code != prop_item.room_code:
                changed_fields.append("room_code")
            if curr_rec.meeting_days != prop_item.meeting_days:
                changed_fields.append("meeting_days")
            if curr_rec.time_slot_id != prop_item.time_slot_id:
                changed_fields.append("time_slot_id")
            status = "modified" if changed_fields else "unchanged"
        else:
            status = "unchanged"
            changed_fields = []

        changed_values.append(
            {
                "class_id": cid,
                "subject_code": b_class.subject_code,
                "status": status,
                "changed_fields": changed_fields,
                "current_assignment": curr_detail,
                "proposed_assignment": prop_detail,
            }
        )

    # 5. Build combined candidate and validate
    other_assignments = [
        Assignment(
            class_id=r.class_id,
            faculty_id=r.faculty_id,
            time_slot_id=r.time_slot_id,
            room_code=r.room_code,
            meeting_days=r.meeting_days,
        )
        for r in schedule.records
        if r.class_id not in block_classes_by_id
        and r.class_id is not None
        and r.time_slot_id is not None
    ]
    proposed_assignments = [
        Assignment(
            class_id=a.class_id,
            faculty_id=a.faculty_id,
            time_slot_id=a.time_slot_id,
            room_code=a.room_code,
            meeting_days=a.meeting_days,
        )
        for a in proposed_raw
    ]
    combined_candidate = CandidateSchedule(
        schedule_id=schedule.schedule_id,
        term_id=schedule.term_id,
        assignments=tuple(other_assignments + proposed_assignments),
    )
    validation_result = _validate_candidate(problem, combined_candidate)
    if payload.strict_class_ids:
        _refuse_conflicting_edit(
            validation_result,
            set(payload.strict_class_ids),
            block_classes_by_id,
            proposed_by_class,
        )

    # 6. Generate preview token
    schedule_hash = _compute_schedule_hash(schedule)
    term_inputs_hash = _compute_term_inputs_hash(problem)
    preview_token = _create_preview_token(
        schedule_id=schedule.schedule_id,
        block_id=block_id,
        schedule_hash=schedule_hash,
        term_inputs_hash=term_inputs_hash,
        assignments=proposed_raw,
    )

    return {
        "preview_token": preview_token,
        "schedule_id": schedule.schedule_id,
        "block_id": block_id,
        "proposed_assignments": proposed_details,
        "changed_values": changed_values,
        "validation": serialize_validation(validation_result),
    }


def _refuse_conflicting_edit(
    validation_result: Any,
    edited_class_ids: set[UUID],
    block_classes_by_id: dict[UUID, Any],
    proposed_by_class: dict[UUID, Any],
) -> None:
    """Refuse a review edit the independent validator finds a hard conflict in.

    Only conflicts touching the edited classes count. They are judged against
    every accepted assignment of the other Blocks and the rest of this Block's
    proposal, so an edit is never applied on top of a double-booking.
    """
    foreign = sorted(str(cid) for cid in edited_class_ids - block_classes_by_id.keys())
    if foreign:
        raise APIError(
            422,
            "INVALID_ASSIGNMENT",
            "An edited class does not belong to this Block.",
            {"class_ids": foreign},
        )
    unplaced = sorted(str(cid) for cid in edited_class_ids - proposed_by_class.keys())
    if unplaced:
        raise APIError(
            422,
            "INVALID_ASSIGNMENT",
            "An edited class has no assignment in the proposal.",
            {"class_ids": unplaced},
        )
    conflicts = [
        conflict
        for conflict in validation_result.constraint_report.conflicts
        if any(cid in edited_class_ids for cid in conflict.class_ids)
    ]
    if not conflicts:
        return
    more = len(conflicts) - 1
    raise APIError(
        409,
        "PROPOSAL_EDIT_CONFLICT",
        f"The change was not applied. {conflicts[0].message}"
        + (f" ({more} more conflict{'s' if more != 1 else ''}.)" if more else ""),
        {
            "conflicts": [
                {
                    "code": str(conflict.code),
                    "resource_key": list(conflict.resource_key),
                    "class_ids": [str(cid) for cid in conflict.class_ids],
                    "time_slot_ids": [str(tid) for tid in conflict.time_slot_ids],
                    "message": conflict.message,
                }
                for conflict in conflicts
            ]
        },
    )


def _fill_missing_shared_peers(
    db: Session,
    schedule: Schedule,
    problem: Any,
    block_id: UUID,
    block_class_ids: set[UUID],
    assignments: Sequence[ReplacementAssignmentPayload],
) -> list[ScheduleRecord]:
    """Write each placed shared session onto approved peers that lack it.

    Only a peer Block that already holds approved records is filled; one not
    generated yet picks the session up as a shared placement when it is.
    Existing peer records are never overwritten.
    """
    sections = {s.class_id: s for s in problem.classes}
    slots = {s.time_slot_id: s for s in problem.time_slots}
    others = [r for r in schedule.records if r.class_id not in block_class_ids]
    recorded = {r.class_id for r in others}
    approved_blocks = {
        sections[r.class_id].block_id for r in others if r.class_id in sections
    }
    filled: list[ScheduleRecord] = []
    for a in assignments:
        offering_id = sections[a.class_id].offering_id
        for peer in problem.classes:
            if (
                peer.offering_id != offering_id
                or peer.block_id == block_id
                or peer.class_id in recorded
                or peer.block_id not in approved_blocks
            ):
                continue
            record = ScheduleRecord(
                schedule_id=schedule.schedule_id,
                term_id=schedule.term_id,
                faculty_id=a.faculty_id,
                subject_code=peer.subject_code,
                class_id=peer.class_id,
                time_slot_id=a.time_slot_id,
                room_code=a.room_code,
                meeting_days=a.meeting_days,
                time_slot=slots[a.time_slot_id].label,
                is_historical=False,
            )
            db.add(record)
            recorded.add(peer.class_id)
            filled.append(record)
    if filled:
        db.flush()
    return filled


def confirm_block_replacement(
    db: Session,
    schedule_id: UUID,
    block_id: UUID,
    payload: BlockReplacementConfirmRequest,
    actor_id: UUID | None = None,
) -> tuple[Schedule, Any]:
    schedule = db.scalar(
        select(Schedule)
        .options(_RECORDS_WITH_PROVENANCE)
        .where(Schedule.schedule_id == schedule_id)
        .with_for_update()
    )
    if schedule is None:
        raise APIError(404, "NOT_FOUND", "The requested schedule was not found.")
    _require_editable(schedule)

    block = db.get(BlockSection, block_id)
    if block is None or block.term_id != schedule.term_id:
        raise APIError(
            404,
            "NOT_FOUND",
            "The requested block was not found for this schedule's term.",
        )

    token_data = _verify_preview_token(
        payload.preview_token,
        expected_schedule_id=schedule_id,
        expected_block_id=block_id,
    )
    # Approving locks the Block, so replacing an approved week needs it
    # unlocked first: nothing locked changes without that explicit step.
    if _is_locked(schedule, block_id):
        raise APIError(
            409,
            "BLOCK_LOCKED",
            f"{block.name} is approved and locked. Unlock it before replacing its week.",
            {"block_ids": [str(block_id)]},
        )

    # Concurrency / Staleness checks
    current_schedule_hash = _compute_schedule_hash(schedule)
    if current_schedule_hash != token_data["schedule_hash"]:
        raise APIError(
            409,
            "STALE_PREVIEW",
            "The schedule has been modified since the replacement preview was generated.",
        )

    _share_lock_faculty(db)
    problem = _get_problem(db, schedule.term_id)
    current_term_inputs_hash = _compute_term_inputs_hash(problem)
    if current_term_inputs_hash != token_data["term_inputs_hash"]:
        raise APIError(
            409,
            "STALE_PREVIEW",
            "Current-term scheduling data has changed since the replacement preview was generated.",
        )

    # Determine assignments to apply
    if payload.assignments is not None:
        user_hash = _compute_assignments_hash(payload.assignments)
        if user_hash != token_data["assignments_hash"]:
            raise APIError(
                400,
                "INVALID_PREVIEW_TOKEN",
                "The provided assignments do not match the preview token.",
            )
        assignments_to_apply = payload.assignments
    else:
        assignments_to_apply = [
            ReplacementAssignmentPayload(
                class_id=UUID(a["class_id"]),
                faculty_id=UUID(a["faculty_id"]),
                room_code=a["room_code"],
                meeting_days=a["meeting_days"],
                time_slot_id=UUID(a["time_slot_id"]),
            )
            for a in token_data["assignments"]
        ]

    block_classes = [c for c in problem.classes if c.block_id == block_id]
    block_class_ids = {c.class_id for c in block_classes}
    sections = {s.class_id: s for s in problem.classes}
    slots = {s.time_slot_id: s for s in problem.time_slots}

    savepoint = db.begin_nested()
    try:
        # 1. Delete old assignments for this block
        records_to_delete = [
            r for r in schedule.records if r.class_id in block_class_ids
        ]
        replaced = [_audit_assignment(r) for r in records_to_delete]
        for r in records_to_delete:
            db.delete(r)
        db.flush()

        # 2. Insert new assignments
        for a in assignments_to_apply:
            sec = sections[a.class_id]
            slot = slots[a.time_slot_id]
            new_record = ScheduleRecord(
                schedule_id=schedule.schedule_id,
                term_id=schedule.term_id,
                faculty_id=a.faculty_id,
                subject_code=sec.subject_code,
                class_id=a.class_id,
                time_slot_id=a.time_slot_id,
                room_code=a.room_code,
                meeting_days=a.meeting_days,
                time_slot=slot.label,
                is_historical=False,
            )
            db.add(new_record)
        db.flush()

        # 2b. A shared lecture is one session for every Block taking it, so
        # an approved partner that has no record for it yet receives the
        # placement too (placing it by hand on one Block used to leave the
        # partner missing the subject while it still looked complete).
        filled = _fill_missing_shared_peers(
            db, schedule, problem, block_id, block_class_ids, assignments_to_apply
        )

        # Expire records collection to ensure reload sees fresh state
        db.expire(schedule, ["records"])

        # 3. Validate new schedule state
        validation_result = _validate_candidate(problem, _to_candidate(schedule))
        # A filled peer that would clash where it lands is taken back out:
        # the partner keeps its missing class rather than gain a conflict.
        clashing = {
            cid
            for conflict in validation_result.constraint_report.conflicts
            for cid in conflict.class_ids
        }
        dropped = [record for record in filled if record.class_id in clashing]
        if dropped:
            for record in dropped:
                db.delete(record)
            db.flush()
            db.expire(schedule, ["records"])
            filled = [record for record in filled if record not in dropped]
            validation_result = _validate_candidate(
                problem, _to_candidate(schedule)
            )
        _require_teaching_days(validation_result, block_class_ids)
        unavailable = next(
            (
                conflict
                for conflict in validation_result.constraint_report.conflicts
                if conflict.code == ConflictCode.FACULTY_UNAVAILABLE
                and any(cid in block_class_ids for cid in conflict.class_ids)
            ),
            None,
        )
        if unavailable is not None:
            raise APIError(
                409,
                "FACULTY_UNAVAILABLE",
                "The faculty member is unavailable for the selected time slot.",
                {
                    "class_ids": [str(cid) for cid in unavailable.class_ids],
                    "time_slot_ids": [
                        str(tid) for tid in unavailable.time_slot_ids
                    ],
                },
            )

        # Approved means locked: the new week is read-only until unlocked.
        for row in list(schedule.unlocked_blocks):
            if row.block_id == block_id:
                schedule.unlocked_blocks.remove(row)
        record_event(
            db,
            actor_id=actor_id,
            operation="schedule.block_replace",
            target_type="schedule",
            target_id=schedule_id,
            details={
                "block_id": block_id,
                "before": replaced,
                "after": [_audit_assignment(a) for a in assignments_to_apply],
                "shared_sessions_filled": [_audit_assignment(r) for r in filled],
            },
        )
        savepoint.commit()
    except (APIError, ConstraintEvaluationError):
        savepoint.rollback()
        db.expire(schedule, ["records"])
        raise

    db.commit()
    return get_schedule(db, schedule_id), validation_result
