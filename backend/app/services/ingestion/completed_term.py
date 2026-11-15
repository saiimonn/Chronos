"""CHR-110 / CHR-ING-002 approved, validated MASTER history capture."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import Assignment, CandidateSchedule
from app.domain.calendar import academic_year_start
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    Room,
    Schedule,
    ScheduleRecord,
    ScheduleType,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.schemas.historical_capture import HistoricalCaptureRequest
from app.services.audit import record_event
from app.services.validation.engine import (
    ConstraintEvaluationError,
    StaleFacultyMaskError,
)
from app.services.validation.validator import ScheduleValidator


def read_historical_capture(db: Session, term_id: UUID) -> dict:
    return _summary(db, _find_snapshot(db, term_id))


def list_historical_records(db: Session, term_id: UUID) -> list[HistoricalRecord]:
    """The term's captured or imported classes, read-only, in a stable order."""
    snapshot = _find_snapshot(db, term_id)
    return list(
        db.scalars(
            select(HistoricalRecord)
            .where(HistoricalRecord.snapshot_id == snapshot.snapshot_id)
            .order_by(
                HistoricalRecord.faculty_name,
                HistoricalRecord.subject_code,
                HistoricalRecord.meeting_days,
                HistoricalRecord.time_slot,
                HistoricalRecord.historical_record_id,
            )
        )
    )


def _find_snapshot(db: Session, term_id: UUID) -> HistoricalSnapshot:
    term = db.get(Term, term_id)
    if term is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    snapshot = db.scalar(
        select(HistoricalSnapshot).where(HistoricalSnapshot.source_term_id == term_id)
    )
    if snapshot is None:
        snapshot = db.scalar(
            select(HistoricalSnapshot).where(
                HistoricalSnapshot.academic_year == term.academic_year,
                HistoricalSnapshot.term_number == term.term_number,
            )
        )
    if snapshot is None:
        raise APIError(
            404, "HISTORY_NOT_CAPTURED", "No historical snapshot exists for this term."
        )
    return snapshot


def _summary(db: Session, snapshot: HistoricalSnapshot) -> dict:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "source_term_id": snapshot.source_term_id,
        "source_schedule_id": snapshot.source_schedule_id,
        "approved_by_user_id": snapshot.approved_by_user_id,
        "academic_year": snapshot.academic_year,
        "term_number": snapshot.term_number,
        "captured_at": snapshot.captured_at,
        "record_count": db.scalar(
            select(func.count())
            .select_from(HistoricalRecord)
            .where(HistoricalRecord.snapshot_id == snapshot.snapshot_id)
        ),
    }


def capture_completed_term(
    db: Session,
    term_id: UUID,
    request: HistoricalCaptureRequest,
    *,
    approved_by_user_id: UUID,
) -> dict:
    try:
        return _capture_completed_term(
            db, term_id, request, approved_by_user_id=approved_by_user_id
        )
    except DBAPIError as error:
        db.rollback()
        raise APIError(
            409,
            "CAPTURE_SOURCE_CHANGED",
            "The capture transaction failed; refresh its source state before retrying.",
        ) from error


def _capture_completed_term(
    db: Session,
    term_id: UUID,
    request: HistoricalCaptureRequest,
    *,
    approved_by_user_id: UUID,
) -> dict:
    """Capture current validated values, never infer completion from inactivity."""
    if db.new or db.dirty or db.deleted:
        raise APIError(
            409,
            "CAPTURE_PENDING_CHANGES",
            "Capture requires a session without pending changes.",
        )
    # Authentication may already have opened a READ COMMITTED transaction. Lock
    # its source/input rows rather than changing isolation after the first read.
    db.expire_all()
    term = db.scalar(select(Term).where(Term.term_id == term_id).with_for_update())
    if term is None:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.")
    if term.is_archived:
        # An archived, captured term would be hidden from every list (history
        # lists skip archived terms; the archive list skips captured ones), so
        # it must be restored before it is captured (CHR-240).
        raise APIError(
            409,
            "TERM_SOFT_ARCHIVED",
            "This term is archived. Restore it before capturing it as history.",
        )
    if term.is_active:
        raise APIError(
            409, "TERM_NOT_COMPLETED", "An active term cannot be captured as history."
        )
    try:
        academic_year_start(term.academic_year)
    except ValueError as error:
        raise APIError(
            409, "INVALID_CAPTURE_SOURCE", "The term has an invalid academic period."
        ) from error
    if (
        db.scalar(
            select(HistoricalSnapshot.snapshot_id)
            .where(
                or_(
                    HistoricalSnapshot.source_term_id == term_id,
                    (HistoricalSnapshot.academic_year == term.academic_year)
                    & (HistoricalSnapshot.term_number == term.term_number),
                )
            )
            .limit(1)
        )
        is not None
    ):
        raise APIError(
            409,
            "HISTORY_ALREADY_CAPTURED",
            "This source term or academic period was already captured.",
        )
    schedule = db.scalar(
        select(Schedule)
        .where(Schedule.schedule_id == request.schedule_id, Schedule.term_id == term_id)
        .with_for_update()
    )
    if schedule is None:
        raise APIError(
            404, "NOT_FOUND", "The requested schedule was not found in this term."
        )
    if schedule.schedule_type != ScheduleType.MASTER.value:
        raise APIError(
            409,
            "CAPTURE_REQUIRES_MASTER",
            "Only the term's master schedule may be captured.",
        )
    if schedule.finalized_at is None:
        raise APIError(
            409,
            "SCHEDULE_NOT_FINALIZED",
            "The master schedule must be finalized before capture.",
        )

    records = db.scalars(
        select(ScheduleRecord)
        .where(ScheduleRecord.schedule_id == schedule.schedule_id)
        .order_by(ScheduleRecord.record_id)
        .with_for_update()
    ).all()
    if not records or any(
        record.is_historical or record.class_id is None or record.time_slot_id is None
        for record in records
    ):
        raise APIError(
            409,
            "INVALID_CAPTURE_SOURCE",
            "The master schedule has invalid source assignment provenance.",
        )
    # Exclusive parent locks also block FK inserts that could change required
    # class membership while validation and denormalization are in progress.
    for model, identity in (
        (BlockSection, BlockSection.block_id),
        (SubjectOffering, SubjectOffering.offering_id),
        (BlockSectionOffering, BlockSectionOffering.class_id),
        (TimeSlot, TimeSlot.time_slot_id),
    ):
        db.execute(
            select(identity)
            .where(model.term_id == term_id)
            .order_by(identity)
            .with_for_update()
        ).all()
    for model, identity in (
        (Faculty, Faculty.faculty_id),
        (Subject, Subject.subject_code),
        (Room, Room.room_code),
        (CourseProgram, CourseProgram.program_code),
    ):
        db.execute(select(identity).order_by(identity).with_for_update(read=True)).all()

    try:
        problem = SQLAlchemySchedulingProblemProvider(db).get_problem(term_id)
        candidate = CandidateSchedule(
            term_id=term_id,
            schedule_id=schedule.schedule_id,
            assignments=tuple(
                Assignment(
                    class_id=record.class_id,
                    faculty_id=record.faculty_id,
                    room_code=record.room_code,
                    meeting_days=record.meeting_days,
                    time_slot_id=record.time_slot_id,
                )
                for record in records
            ),
        )
        result = ScheduleValidator().validate(problem, candidate)
    except StaleFacultyMaskError as error:
        # Name the member whose availability needs re-saving.
        raise APIError(
            409,
            "FACULTY_MASK_STALE",
            str(error),
            {"faculty_id": str(error.faculty_id)},
        ) from error
    except (ConstraintEvaluationError, ValueError) as error:
        raise APIError(
            409,
            "INVALID_CAPTURE_SOURCE",
            "The master schedule or its current inputs are invalid.",
        ) from error
    if not result.is_export_eligible:
        raise APIError(
            409,
            "SCHEDULE_NOT_CAPTUREABLE",
            "Capture requires 100% completeness and zero hard conflicts.",
            {
                "required_class_count": result.required_class_count,
                "assigned_class_count": result.assigned_class_count,
                "hard_conflict_count": result.hard_conflict_count,
            },
        )

    classes = {item.class_id: item for item in problem.classes}
    faculty = {item.faculty_id: item for item in problem.faculty}
    subjects = {item.subject_code: item for item in problem.subjects}
    slots = {item.time_slot_id: item for item in problem.time_slots}
    meetings = {}
    for record in records:
        offering = classes[record.class_id].offering_id
        placement = (
            record.faculty_id,
            record.room_code,
            record.meeting_days,
            record.time_slot_id,
        )
        if offering in meetings and meetings[offering][0] != placement:
            raise APIError(
                409,
                "INCONSISTENT_SHARED_OFFERING",
                "Blocks sharing an Offering must describe one teaching meeting.",
            )
        meetings.setdefault(offering, (placement, record))

    snapshot = HistoricalSnapshot(
        source_term_id=term_id,
        source_schedule_id=schedule.schedule_id,
        approved_by_user_id=approved_by_user_id,
        academic_year=term.academic_year,
        term_number=term.term_number,
    )
    try:
        with db.begin_nested():
            db.add(snapshot)
            db.flush()
            for _placement, record in meetings.values():
                slot = slots[record.time_slot_id]
                if any(
                    (
                        slot.start_time.second,
                        slot.start_time.microsecond,
                        slot.end_time.second,
                        slot.end_time.microsecond,
                    )
                ):
                    raise APIError(
                        409,
                        "INVALID_CAPTURE_SOURCE",
                        "Historical capture requires exact whole-minute slot boundaries.",
                    )
                db.add(
                    HistoricalRecord(
                        snapshot_id=snapshot.snapshot_id,
                        source_record_id=record.record_id,
                        source_faculty_id=record.faculty_id,
                        faculty_name=faculty[record.faculty_id].full_name,
                        subject_code=record.subject_code,
                        subject_title=subjects[record.subject_code].subject_title,
                        room_code=record.room_code,
                        meeting_days=record.meeting_days,
                        time_slot=f"{slot.start_time:%H:%M}-{slot.end_time:%H:%M}",
                    )
                )
            db.flush()
        record_event(
            db,
            actor_id=approved_by_user_id,
            operation="historical_capture.create",
            target_type="term",
            target_id=term_id,
            details={
                "snapshot_id": snapshot.snapshot_id,
                "schedule_id": schedule.schedule_id,
                "academic_year": term.academic_year,
                "term_number": term.term_number,
                "record_count": len(meetings),
            },
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise APIError(
            409,
            "HISTORY_CAPTURE_CONFLICT",
            "The history capture conflicted with persisted data; no partial capture was retained.",
        ) from error
    except DBAPIError as error:
        db.rollback()
        raise APIError(
            409,
            "CAPTURE_SOURCE_CHANGED",
            "The capture transaction failed; refresh its source state before retrying.",
        ) from error
    return _summary(db, snapshot)
