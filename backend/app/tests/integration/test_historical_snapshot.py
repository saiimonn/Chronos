"""PostgreSQL guarantees for CHR-23 / CHR-DB-004 immutable historical data."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    Room,
    ScheduleRecord,
    Subject,
    Term,
)

pytestmark = pytest.mark.postgres

IMMUTABLE = "historical data is immutable"


def make_finalized_term(session: Session, suffix: str = ""):
    """A past term as the operational tables hold it, before it is captured."""
    term = Term(
        academic_year="2023-2024" if not suffix else "2022-2023",
        term_number=1,
        is_active=False,
    )
    faculty = Faculty(full_name="<FACULTY>", is_full_time=False, availability_mask="1")
    subject = Subject(
        subject_code=f"CS40{suffix or '0'}",
        subject_title="<SUBJECT>",
        units=3,
        subject_type="LEC",
    )
    room = Room(
        room_code=f"LAB{suffix or '1'}",
        building_name="<BUILDING>",
        capacity=20,
        room_type="laboratory",
        expanded=False,
    )
    session.add_all([term, faculty, subject, room])
    session.flush()

    source_record = ScheduleRecord(
        faculty_id=faculty.faculty_id,
        subject_code=subject.subject_code,
        term_id=term.term_id,
        room_code=room.room_code,
        meeting_days="MWF",
        time_slot="07:30-08:30",
        is_historical=True,
    )
    session.add(source_record)
    session.flush()
    return term, faculty, subject, room, source_record


def capture(session: Session, sources) -> tuple[HistoricalSnapshot, HistoricalRecord]:
    """Denormalize one finalized term into a snapshot, as ingestion will."""
    term, faculty, subject, room, source_record = sources
    snapshot = HistoricalSnapshot(
        source_term_id=term.term_id,
        academic_year=term.academic_year,
        term_number=term.term_number,
    )
    session.add(snapshot)
    session.flush()

    record = HistoricalRecord(
        snapshot_id=snapshot.snapshot_id,
        source_record_id=source_record.record_id,
        source_faculty_id=faculty.faculty_id,
        faculty_name=faculty.full_name,
        subject_code=subject.subject_code,
        subject_title=subject.subject_title,
        room_code=room.room_code,
        meeting_days=source_record.meeting_days,
        time_slot=source_record.time_slot,
    )
    session.add(record)
    session.flush()
    return snapshot, record


def test_snapshot_persists_the_denormalized_offering(db_session: Session):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)
    term, faculty, subject, room, source_record = sources

    assert snapshot.source_term_id == term.term_id
    assert snapshot.academic_year == "2023-2024"
    assert snapshot.captured_at is not None
    assert snapshot.records == [record]
    assert record.source_record_id == source_record.record_id
    assert record.source_faculty_id == faculty.faculty_id
    assert record.faculty_name == faculty.full_name
    assert record.subject_code == subject.subject_code
    assert record.subject_title == subject.subject_title
    assert record.room_code == room.room_code
    assert (record.meeting_days, record.time_slot) == ("MWF", "07:30-08:30")


def test_editing_the_sources_does_not_reach_captured_history(db_session: Session):
    sources = make_finalized_term(db_session)
    _, record = capture(db_session, sources)
    term, faculty, subject, room, source_record = sources
    original_faculty_id = faculty.faculty_id

    faculty.full_name = "<RENAMED FACULTY>"
    subject.subject_title = "<RETITLED SUBJECT>"
    room.capacity = 10
    db_session.flush()
    db_session.expire_all()

    assert record.faculty_name == "<FACULTY>"
    assert record.subject_title == "<SUBJECT>"
    assert record.source_faculty_id == original_faculty_id
    assert record.room_code == room.room_code


def test_history_outlives_the_operational_rows_it_was_captured_from(
    db_session: Session,
):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)
    term, faculty, subject, room, source_record = sources
    record_id = record.historical_record_id

    # Nothing in the snapshot references these rows, so a term cleanup that
    # removes them cannot cascade into, or be blocked by, history.
    db_session.delete(source_record)
    db_session.flush()
    db_session.delete(room)
    db_session.delete(subject)
    db_session.delete(faculty)
    db_session.delete(term)
    db_session.flush()
    db_session.expire_all()

    surviving = db_session.get(HistoricalRecord, record_id)
    assert surviving is not None
    assert surviving.faculty_name == "<FACULTY>"
    assert surviving.subject_code.startswith("CS40")
    assert surviving.room_code.startswith("LAB")
    assert surviving.snapshot.academic_year == "2023-2024"


def test_operational_edit_of_a_historical_record_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    _, record = capture(db_session, sources)

    record.time_slot = "13:00-14:00"

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        db_session.flush()


def test_direct_update_leaves_the_historical_record_unchanged(db_session: Session):
    sources = make_finalized_term(db_session)
    _, record = capture(db_session, sources)
    record_id = record.historical_record_id

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE historical_record SET time_slot = '13:00-14:00' "
                    "WHERE historical_record_id = :record_id"
                ),
                {"record_id": record_id},
            )
    db_session.expire_all()

    assert db_session.get(HistoricalRecord, record_id).time_slot == "07:30-08:30"


def test_deleting_a_historical_record_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    _, record = capture(db_session, sources)
    record_id = record.historical_record_id

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        with db_session.begin_nested():
            db_session.delete(record)
            db_session.flush()
    db_session.expire_all()

    assert db_session.get(HistoricalRecord, record_id) is not None


def test_updating_a_snapshot_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    snapshot, _ = capture(db_session, sources)

    snapshot.academic_year = "2024-2025"

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        db_session.flush()


def test_deleting_a_snapshot_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    snapshot, _ = capture(db_session, sources)
    snapshot_id = snapshot.snapshot_id

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        with db_session.begin_nested():
            db_session.delete(snapshot)
            db_session.flush()
    db_session.expire_all()

    assert db_session.get(HistoricalSnapshot, snapshot_id) is not None


@pytest.mark.parametrize(
    "table_name", ["historical_record", "historical_snapshot"]
)
def test_truncating_a_historical_table_is_rejected(
    db_session: Session, table_name: str
):
    sources = make_finalized_term(db_session)
    capture(db_session, sources)

    with pytest.raises(IntegrityError, match=IMMUTABLE):
        with db_session.begin_nested():
            db_session.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
    db_session.expire_all()

    remaining = db_session.scalar(
        select(func.count()).select_from(HistoricalRecord)
    )
    assert remaining == 1


def test_capturing_the_same_source_record_twice_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)

    db_session.add(
        HistoricalRecord(
            snapshot_id=snapshot.snapshot_id,
            source_record_id=record.source_record_id,
            source_faculty_id=record.source_faculty_id,
            faculty_name=record.faculty_name,
            subject_code=record.subject_code,
            subject_title=record.subject_title,
            room_code=record.room_code,
            meeting_days=record.meeting_days,
            time_slot=record.time_slot,
        )
    )

    with pytest.raises(IntegrityError, match="uq_historical_record_source_record"):
        db_session.flush()


def test_capturing_one_source_record_into_two_snapshots_is_rejected(
    db_session: Session,
):
    sources = make_finalized_term(db_session)
    _, record = capture(db_session, sources)
    other_sources = make_finalized_term(db_session, suffix="B")
    other_snapshot, _ = capture(db_session, other_sources)

    # A legacy record moved to another term would otherwise be captured twice
    # and counted twice by ARM, with neither copy removable.
    db_session.add(
        HistoricalRecord(
            snapshot_id=other_snapshot.snapshot_id,
            source_record_id=record.source_record_id,
            source_faculty_id=record.source_faculty_id,
            faculty_name=record.faculty_name,
            subject_code=record.subject_code,
            subject_title=record.subject_title,
            room_code=record.room_code,
            meeting_days=record.meeting_days,
            time_slot=record.time_slot,
        )
    )

    with pytest.raises(IntegrityError, match="uq_historical_record_source_record"):
        db_session.flush()


@pytest.mark.parametrize(
    "blank_field",
    ["faculty_name", "subject_code", "subject_title", "meeting_days", "time_slot"],
)
def test_blank_required_values_are_rejected(db_session: Session, blank_field: str):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)
    values = {
        "snapshot_id": snapshot.snapshot_id,
        "source_record_id": None,
        "source_faculty_id": record.source_faculty_id,
        "faculty_name": record.faculty_name,
        "subject_code": record.subject_code,
        "subject_title": record.subject_title,
        "meeting_days": record.meeting_days,
        "time_slot": record.time_slot,
        blank_field: "  ",
    }
    db_session.add(HistoricalRecord(**values))

    with pytest.raises(
        IntegrityError, match=f"ck_historical_record_{blank_field}_not_blank"
    ):
        db_session.flush()


def test_snapshot_records_read_back_in_a_stable_order(db_session: Session):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)
    db_session.add_all(
        HistoricalRecord(
            snapshot_id=snapshot.snapshot_id,
            source_faculty_id=record.source_faculty_id,
            faculty_name=record.faculty_name,
            subject_code=record.subject_code,
            subject_title=record.subject_title,
            meeting_days="TTh",
            time_slot=slot,
        )
        for slot in ("09:00-10:30", "10:30-12:00")
    )
    db_session.flush()
    db_session.expire_all()

    read_ids = [row.historical_record_id for row in snapshot.records]
    assert read_ids == sorted(read_ids)


def test_imported_records_without_an_operational_origin_may_coexist(
    db_session: Session,
):
    sources = make_finalized_term(db_session)
    snapshot, record = capture(db_session, sources)
    imported = [
        HistoricalRecord(
            snapshot_id=snapshot.snapshot_id,
            source_record_id=None,
            source_faculty_id=record.source_faculty_id,
            faculty_name=record.faculty_name,
            subject_code=record.subject_code,
            subject_title=record.subject_title,
            room_code=None,
            meeting_days="TTh",
            time_slot=slot,
        )
        for slot in ("09:00-10:30", "10:30-12:00")
    ]
    db_session.add_all(imported)
    db_session.flush()

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(HistoricalRecord)
            .where(HistoricalRecord.snapshot_id == snapshot.snapshot_id)
        )
        == 3
    )


def test_capturing_one_term_twice_is_rejected(db_session: Session):
    sources = make_finalized_term(db_session)
    capture(db_session, sources)
    term = sources[0]

    db_session.add(
        HistoricalSnapshot(
            source_term_id=term.term_id,
            academic_year=term.academic_year,
            term_number=term.term_number,
        )
    )

    with pytest.raises(IntegrityError, match="uq_historical_snapshot_source_term"):
        db_session.flush()


def test_capturing_the_same_academic_period_under_a_new_term_is_rejected(
    db_session: Session,
):
    sources = make_finalized_term(db_session)
    capture(db_session, sources)
    term = sources[0]

    # A term row deleted and re-created would carry a different identifier while
    # describing the same period; ARM would then count it twice.
    db_session.add(
        HistoricalSnapshot(
            source_term_id=uuid.uuid4(),
            academic_year=term.academic_year,
            term_number=term.term_number,
        )
    )

    with pytest.raises(IntegrityError, match="uq_historical_snapshot_term_period"):
        db_session.flush()
