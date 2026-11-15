"""CHR-108 / CHR-ING-001 reference checks and atomic append-only import."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    Room,
    Subject,
    Term,
)
from app.services.ingestion.historical_file import (
    HistoricalImportData,
    HistoricalImportError,
)


@dataclass(frozen=True, slots=True)
class HistoricalImportValidation:
    term_ids: tuple[tuple[tuple[str, int], UUID], ...]
    unknown_subject_count: int
    unknown_room_count: int


def validate_historical_import(
    session: Session, data: HistoricalImportData
) -> HistoricalImportValidation:
    """SELECTs only, including when the caller has unrelated pending changes."""
    periods = set(data.periods)
    faculty_ids = {record.source_faculty_id for record in data.records}
    record_ids = {record.historical_record_id for record in data.records}
    subjects = {record.subject_code for record in data.records}
    rooms = {
        record.room_code for record in data.records if record.room_code is not None
    }
    if not data.records or len(record_ids) != len(data.records):
        raise HistoricalImportError(
            "historical import must contain unique, non-empty records"
        )
    with session.no_autoflush:
        known_faculty = set(
            session.scalars(
                select(Faculty.faculty_id).where(Faculty.faculty_id.in_(faculty_ids))
            )
        )
        terms = session.scalars(
            select(Term).where(
                tuple_(Term.academic_year, Term.term_number).in_(periods)
            )
        ).all()
        term_ids = {
            (term.academic_year, term.term_number): term.term_id for term in terms
        }
        captured = session.scalar(
            select(HistoricalSnapshot.snapshot_id)
            .where(
                or_(
                    HistoricalSnapshot.source_term_id.in_(term_ids.values()),
                    tuple_(
                        HistoricalSnapshot.academic_year, HistoricalSnapshot.term_number
                    ).in_(periods),
                )
            )
            .limit(1)
        )
        existing_record = session.scalar(
            select(HistoricalRecord.historical_record_id)
            .where(HistoricalRecord.historical_record_id.in_(record_ids))
            .limit(1)
        )
        known_subjects = set(
            session.scalars(
                select(Subject.subject_code).where(Subject.subject_code.in_(subjects))
            )
        )
        known_rooms = (
            set(
                session.scalars(select(Room.room_code).where(Room.room_code.in_(rooms)))
            )
            if rooms
            else set()
        )
    violations = []
    if faculty_ids - known_faculty:
        violations.append("reviewed faculty IDs are not present in the local catalog")
    if missing := periods - term_ids.keys():
        shown = [f"{year} T{number}" for year, number in sorted(missing)[:10]]
        more = f" and {len(missing) - 10} more" if len(missing) > 10 else ""
        violations.append(
            "historical term periods are not present in the local catalog: "
            f"{', '.join(shown)}{more}"
        )
    if any(term.is_active for term in terms):
        violations.append("active terms cannot be captured as historical snapshots")
    if captured is not None:
        violations.append("a source term or academic period was already captured")
    if existing_record is not None:
        violations.append("a source historical record was already imported")
    if violations:
        raise HistoricalImportError("; ".join(violations))
    return HistoricalImportValidation(
        tuple(sorted(term_ids.items())),
        len(subjects - known_subjects),
        len(rooms - known_rooms),
    )


def import_historical_data(
    session: Session, data: HistoricalImportData
) -> tuple[UUID, ...]:
    """Stage the whole import in one savepoint; the caller owns final commit."""
    # begin_nested flushes pending state even with autoflush disabled.
    if session.new or session.dirty or session.deleted:
        raise HistoricalImportError(
            "historical import requires a session without pending changes"
        )
    session.scalars(
        select(Term)
        .where(tuple_(Term.academic_year, Term.term_number).in_(data.periods))
        .order_by(Term.term_id)
        .with_for_update()
    ).all()
    validation = validate_historical_import(session, data)
    term_ids = dict(validation.term_ids)
    snapshot_ids = {period: uuid4() for period in data.periods}
    try:
        with session.begin_nested():
            session.add_all(
                HistoricalSnapshot(
                    snapshot_id=snapshot_id,
                    source_term_id=term_ids[period],
                    academic_year=period[0],
                    term_number=period[1],
                )
                for period, snapshot_id in snapshot_ids.items()
            )
            session.flush()
            session.add_all(
                HistoricalRecord(
                    historical_record_id=record.historical_record_id,
                    snapshot_id=snapshot_ids[record.period],
                    source_record_id=None,
                    source_faculty_id=record.source_faculty_id,
                    faculty_name=record.faculty_name,
                    subject_code=record.subject_code,
                    subject_title=record.subject_title,
                    room_code=record.room_code,
                    meeting_days=record.meeting_days,
                    time_slot=record.time_slot,
                )
                for record in data.records
            )
            session.flush()
    except IntegrityError as error:
        raise HistoricalImportError(
            "historical import conflicted with persisted data; no partial import retained"
        ) from error
    return tuple(snapshot_ids.values())
