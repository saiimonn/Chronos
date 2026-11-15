"""CHR-162 / CHR-FIX-002: grid membership and availability review serialize.

Independent, committed PostgreSQL transactions are essential: a rollback-only
fixture on one connection cannot reproduce a writer skipping a NULL stamp.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import time
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import sessionmaker

from app.api.routes_time_slots import generate_canonical_slots
from app.core.errors import APIError
from app.domain import DCISM_CALENDAR, generate_windows
from app.models import Faculty, Term, TimeSlot
from app.schemas.time_slot import TimeSlotCreate
from app.services.catalog import availability, terms, time_slots

pytestmark = pytest.mark.postgres

TERM_ID = UUID("16200000-0000-0000-0000-000000000001")
FACULTY_ID = UUID("16200000-0000-0000-0000-000000000002")
NEW_TERM_ID = UUID("16200000-0000-0000-0000-000000000003")
WINDOWS = tuple(generate_windows(DCISM_CALENDAR))
SELECTED_ID = WINDOWS[0].slot_id(TERM_ID)
REMOVED_ID = WINDOWS[1].slot_id(TERM_ID)


@pytest.fixture
def committed_grid(migrated_engine):
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as db:
        db.add(Term(
            term_id=TERM_ID, academic_year="<YEAR-1>", term_number=1,
            is_active=True,
        ))
        db.flush()
        db.add(Faculty(
            faculty_id=FACULTY_ID, full_name="<FACULTY-1>", is_full_time=False,
            availability_mask="10", availability_reviewed_term_id=None,
        ))
        for window in WINDOWS[:2]:
            db.add(TimeSlot(
                time_slot_id=window.slot_id(TERM_ID), term_id=TERM_ID,
                label=window.label, start_time=window.start_time,
                end_time=window.end_time, ordinal=window.ordinal,
            ))
        db.commit()
    try:
        yield make_session
    finally:
        with make_session() as db:
            db.execute(delete(Faculty).where(Faculty.faculty_id == FACULTY_ID))
            db.execute(delete(TimeSlot).where(
                TimeSlot.term_id.in_([TERM_ID, NEW_TERM_ID]),
            ))
            db.execute(delete(Term).where(Term.term_id.in_([TERM_ID, NEW_TERM_ID])))
            db.commit()


def _bounded_session(db):
    # Session-level limits also cover the services' post-commit ORM reloads.
    db.execute(text("SET lock_timeout = '5s'"))
    db.execute(text("SET statement_timeout = '10s'"))
    return db.scalar(text("SELECT pg_backend_pid()"))


def _write_grid(db, operation):
    if operation == "create":
        time_slots.create_time_slot(db, TimeSlotCreate(
            term_id=TERM_ID, label="<MANUAL-WINDOW>", start_time=time(8),
            end_time=time(9, 30), ordinal=len(WINDOWS),
        ))
    elif operation == "delete":
        time_slots.delete_time_slot(db, REMOVED_ID)
    elif operation == "replace":
        time_slots.delete_time_slot(db, SELECTED_ID)
        replacement = time_slots.create_time_slot(db, TimeSlotCreate(
            term_id=TERM_ID, label="<REPLACEMENT-WINDOW>", start_time=time(18),
            end_time=time(19, 30), ordinal=WINDOWS[0].ordinal,
        ))
        return replacement.time_slot_id
    else:
        raise AssertionError(f"Unknown grid operation: {operation}")


def _blocked_query(make_session, waiter_pid, blocker_pid, future):
    """Observe PostgreSQL's actual wait, not merely a thread reaching a hook."""
    deadline = monotonic() + 3
    with make_session() as observer:
        while True:
            blockers = observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid},
            )
            if blocker_pid in blockers:
                return observer.scalar(
                    text("SELECT query FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": waiter_pid},
                ).lower()
            if future.done():
                future.result()  # Never turn an unexpected worker exception into success.
                return None
            assert monotonic() < deadline, "worker neither completed nor blocked"
            sleep(0.01)


@pytest.mark.parametrize("review", ["save", "confirm"])
def test_replacement_after_commit_preserves_review_response_snapshot(
    committed_grid, monkeypatch, review,
):
    make_session = committed_grid

    grid_read = Event()
    release_review = Event()
    writer_started = Event()
    grid_written = Event()
    review_pids, writer_pids = [], []
    original_grid = availability._active_grid

    def read_then_pause(*args, **kwargs):
        captured = original_grid(*args, **kwargs)
        grid_read.set()
        assert release_review.wait(8), "controller did not release the grid reader"
        return captured

    monkeypatch.setattr(availability, "_active_grid", read_then_pause)

    def wait_for_replacement(_db):
        assert grid_written.wait(5), "replacement did not finish after review committed"

    def review_mask():
        with make_session() as db:
            review_pids.append(_bounded_session(db))
            # Let the selected ORM row disappear before response decoding.
            event.listen(db, "after_commit", wait_for_replacement)
            operation = (
                availability.set_availability if review == "save"
                else availability.confirm_availability
            )
            faculty, term_id, slots, selected, _ = operation(
                db, FACULTY_ID, TERM_ID, [SELECTED_ID],
            )
            # Materialize the response while its session is still open. An ORM
            # reload of a concurrently deleted slot is a real failure, not success.
            assert term_id == TERM_ID
            assert faculty.availability_mask == "10"
            assert [slot.time_slot_id for slot in slots] == [SELECTED_ID, REMOVED_ID]
            assert selected == [SELECTED_ID]
            return slots, selected

    def change_grid():
        with make_session() as db:
            writer_pids.append(_bounded_session(db))
            writer_started.set()
            try:
                return _write_grid(db, "replace")
            finally:
                grid_written.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reviewing = pool.submit(review_mask)
        try:
            assert grid_read.wait(5), "review never finished reading the grid"
            writing = pool.submit(change_grid)
            assert writer_started.wait(5)
            assert review_pids[0] != writer_pids[0]
            blocked_sql = _blocked_query(
                make_session, writer_pids[0], review_pids[0], writing,
            )
        finally:
            release_review.set()
        response_slots, response_selected = reviewing.result(timeout=10)
        replacement_id = writing.result(timeout=10)

    with make_session() as db:
        faculty = db.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "10"
        assert faculty.availability_reviewed_term_id is None, (
            "review stamped the old grid after a membership writer skipped NULL"
        )
        ids = set(db.scalars(select(TimeSlot.time_slot_id).where(
            TimeSlot.term_id == TERM_ID,
        )))
        assert replacement_id != SELECTED_ID
        assert ids == {replacement_id, REMOVED_ID}
        _, _, current_slots, current_selected, _ = availability.get_availability(
            db, FACULTY_ID,
        )
        assert len(current_slots) == 2
        assert current_selected == [replacement_id]
        assert SELECTED_ID not in current_selected

    # The response still describes the reviewed grid, even after its selected
    # slot was replaced and both worker sessions have closed.
    assert [slot.time_slot_id for slot in response_slots] == [SELECTED_ID, REMOVED_ID]
    assert response_slots[0].label == WINDOWS[0].label
    assert response_slots[0].start_time == WINDOWS[0].start_time
    assert response_slots[0].end_time == WINDOWS[0].end_time
    assert response_selected == [SELECTED_ID]
    assert blocked_sql is not None, "membership writer did not wait for review"
    assert "select" in blocked_sql and "from term" in blocked_sql
    assert "for no key update" in blocked_sql


@pytest.mark.parametrize("preloaded", [False, True])
def test_save_after_grid_writer_reviews_the_new_grid(committed_grid, preloaded):
    make_session = committed_grid
    with make_session() as db:
        db.get(Faculty, FACULTY_ID).availability_reviewed_term_id = TERM_ID
        db.commit()

    started = Event()
    save_pids = []

    def save():
        with make_session() as db:
            save_pids.append(_bounded_session(db))
            cached = db.get(Faculty, FACULTY_ID) if preloaded else None
            started.set()
            faculty, _, slots, selected, _ = availability.set_availability(
                db, FACULTY_ID, TERM_ID, [SELECTED_ID],
            )
            assert faculty.availability_mask == "1"
            assert faculty.availability_reviewed_term_id == TERM_ID
            assert [slot.time_slot_id for slot in slots] == [SELECTED_ID]
            assert selected == [SELECTED_ID]
            if preloaded:
                assert faculty is cached

    with make_session() as writing, ThreadPoolExecutor(max_workers=1) as pool:
        writer_pid = _bounded_session(writing)
        writing.scalar(select(Term).where(Term.term_id == TERM_ID).with_for_update())
        saving = pool.submit(save)
        try:
            assert started.wait(5)
            assert _blocked_query(make_session, save_pids[0], writer_pid, saving)
            _write_grid(writing, "delete")
            saving.result(timeout=10)
        finally:
            writing.rollback()

    with make_session() as db:
        faculty = db.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "1"
        assert faculty.availability_reviewed_term_id == TERM_ID


def test_canonical_helper_supports_a_new_uncommitted_inactive_term(committed_grid):
    make_session = committed_grid
    with make_session() as db:
        _bounded_session(db)
        db.add(Term(
            term_id=NEW_TERM_ID, academic_year="<YEAR-2>", term_number=1,
            is_active=False,
        ))
        db.flush()
        slots = time_slots.ensure_canonical_slots(db, NEW_TERM_ID)
        assert {slot.time_slot_id for slot in slots} == {
            window.slot_id(NEW_TERM_ID) for window in WINDOWS
        }
        # The helper must not commit a partially created term on its caller's behalf.
        with make_session() as observer:
            assert observer.get(Term, NEW_TERM_ID) is None
        db.commit()

    with make_session() as observer:
        assert observer.get(Term, NEW_TERM_ID).is_active is False
        assert observer.get(Term, TERM_ID).is_active is True
        assert len(time_slots.list_time_slots(observer, NEW_TERM_ID)) == len(WINDOWS)


@pytest.mark.parametrize("writer", ["generate", "create", "delete"])
def test_operational_writer_rechecks_active_term_after_waiting_for_selection(
    committed_grid, writer,
):
    make_session = committed_grid
    with make_session() as db:
        db.add(Term(
            term_id=NEW_TERM_ID, academic_year="<YEAR-2>", term_number=1,
            is_active=False,
        ))
        db.get(Faculty, FACULTY_ID).availability_reviewed_term_id = TERM_ID
        db.commit()

    started = Event()
    writer_pids = []

    def write_old_term():
        with make_session() as db:
            writer_pids.append(_bounded_session(db))
            started.set()
            if writer == "generate":
                generate_canonical_slots(db, TERM_ID)
            else:
                _write_grid(db, writer)

    with make_session() as selecting, ThreadPoolExecutor(max_workers=1) as pool:
        selection_pid = _bounded_session(selecting)
        selecting.scalar(select(Term).where(Term.term_id == TERM_ID).with_for_update())
        writing = pool.submit(write_old_term)
        try:
            assert started.wait(5)
            assert writer_pids[0] != selection_pid
            blocked_sql = _blocked_query(
                make_session, writer_pids[0], selection_pid, writing,
            )
            assert blocked_sql is not None, "writer bypassed the term selection lock"
            assert "select" in blocked_sql and "from term" in blocked_sql
            assert "for no key update" in blocked_sql
            terms.select_term(selecting, NEW_TERM_ID)
            with pytest.raises(APIError) as refused:
                writing.result(timeout=10)
            assert refused.value.status_code == 409
            assert refused.value.code == "TERM_NOT_ACTIVE"
            assert refused.value.details == {
                "active_term_id": str(NEW_TERM_ID),
                "requested_term_id": str(TERM_ID),
            }
        finally:
            selecting.rollback()

    with make_session() as db:
        assert db.get(Term, TERM_ID).is_active is False
        assert db.get(Term, NEW_TERM_ID).is_active is True
        assert set(db.scalars(select(TimeSlot.time_slot_id).where(
            TimeSlot.term_id == TERM_ID,
        ))) == {SELECTED_ID, REMOVED_ID}
        faculty = db.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "10"
        assert faculty.availability_reviewed_term_id == TERM_ID


def test_term_deletion_waits_for_grid_read_before_deleting_slots(committed_grid):
    make_session = committed_grid
    started = Event()
    deletion_pids = []

    def delete_whole_term():
        with make_session() as db:
            deletion_pids.append(_bounded_session(db))
            started.set()
            terms.delete_term(db, TERM_ID)

    with make_session() as reading, ThreadPoolExecutor(max_workers=1) as pool:
        reader_pid = _bounded_session(reading)
        reading.scalar(select(Term).where(
            Term.term_id == TERM_ID,
        ).with_for_update(read=True))
        deleting = pool.submit(delete_whole_term)
        try:
            assert started.wait(5)
            assert deletion_pids[0] != reader_pid
            blocked_sql = _blocked_query(
                make_session, deletion_pids[0], reader_pid, deleting,
            )
            assert blocked_sql is not None, "term deletion bypassed the grid reader"
            assert "select" in blocked_sql and "from term" in blocked_sql
            assert "for no key update" in blocked_sql
            # Visibility alone misses uncommitted DELETEs. NOWAIT proves the
            # deleting transaction has not already removed/locked the slot rows.
            untouched_ids = set(reading.scalars(select(TimeSlot.time_slot_id).where(
                TimeSlot.term_id == TERM_ID,
            ).with_for_update(nowait=True)))
            assert untouched_ids == {SELECTED_ID, REMOVED_ID}
        finally:
            reading.rollback()
        deleting.result(timeout=10)

    with make_session() as db:
        assert db.get(Term, TERM_ID) is None
        assert time_slots.list_time_slots(db, TERM_ID) == []
        faculty = db.get(Faculty, FACULTY_ID)
        assert faculty.availability_mask == "10"
        assert faculty.availability_reviewed_term_id is None
