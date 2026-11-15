"""CHR-162 / CHR-FIX-002 grid membership and availability review serialize."""

from concurrent.futures import ThreadPoolExecutor
from datetime import time
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.errors import APIError
from app.domain import DCISM_CALENDAR, generate_windows
from app.models import Faculty, Schedule, Term, TimeSlot
from app.schemas.time_slot import TimeSlotCreate
from app.services.catalog import availability, time_slots

pytestmark = pytest.mark.postgres
TERM_ID = UUID("16200000-0000-0000-0000-000000000001")
FACULTY_ID = UUID("16200000-0000-0000-0000-000000000002")
WINDOW = next(iter(generate_windows(DCISM_CALENDAR)))
SLOT_ID = WINDOW.slot_id(TERM_ID)


@pytest.fixture
def committed_grid(migrated_engine):
    make_session = sessionmaker(bind=migrated_engine, autoflush=False)
    with make_session() as db:
        db.add(Term(
            term_id=TERM_ID, academic_year="2025-2026", term_number=1, is_active=True,
        ))
        db.flush()
        db.add_all([
            Faculty(
                faculty_id=FACULTY_ID, full_name="<FACULTY-GRID-RACE>",
                is_full_time=True, availability_mask="1",
            ),
            TimeSlot(
                time_slot_id=SLOT_ID, term_id=TERM_ID, label=WINDOW.label,
                start_time=WINDOW.start_time, end_time=WINDOW.end_time, ordinal=WINDOW.ordinal,
            ),
        ])
        db.commit()
    try:
        yield make_session
    finally:
        with make_session() as db:
            db.execute(delete(TimeSlot).where(TimeSlot.term_id == TERM_ID))
            db.execute(delete(Faculty).where(Faculty.faculty_id == FACULTY_ID))
            db.execute(delete(Term).where(Term.term_id == TERM_ID))
            db.commit()


def _change_grid(db, change):
    if change == "generate":
        time_slots.ensure_canonical_slots(db, TERM_ID)
        db.commit()
        return
    if change == "replace":
        time_slots.delete_time_slot(db, SLOT_ID)
    time_slots.create_time_slot(db, TimeSlotCreate(
        term_id=TERM_ID, label="<REPLACEMENT-WINDOW>",
        start_time=time(12), end_time=time(13, 30), ordinal=0 if change == "replace" else 1,
    ))


def _wait_for_blocker(make_session, owner_pid, contender_pid, future):
    deadline = monotonic() + 5
    with make_session() as observer:
        while owner_pid not in observer.scalar(
            text("SELECT pg_blocking_pids(:pid)"), {"pid": contender_pid},
        ):
            if future.done():
                future.result()
                pytest.fail("availability did not wait for the grid transaction")
            assert monotonic() < deadline
            sleep(0.01)


@pytest.mark.parametrize("operation", ["save", "confirm"])
@pytest.mark.parametrize("change", ["create", "replace", "generate"])
def test_grid_changes_wait_for_unreviewed_availability(
    committed_grid, monkeypatch, operation, change,
):
    make_session = committed_grid
    outcomes = []
    method = "encode" if operation == "save" else "available_slot_ids"
    original = getattr(availability._codec, method)

    def change_while_reviewing():
        with make_session() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            try:
                _change_grid(db, change)
            except OperationalError as error:
                if error.orig.pgcode != "55P03":
                    raise
                return "waited"
        return "changed"

    def read_then_interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        if not outcomes:
            with ThreadPoolExecutor(max_workers=1) as pool:
                outcomes.append(pool.submit(change_while_reviewing).result(timeout=5))
            assert outcomes == ["waited"], "grid changed after the reviewed grid was read"
        return result

    monkeypatch.setattr(availability._codec, method, read_then_interleave)
    review = availability.set_availability if operation == "save" else availability.confirm_availability
    with make_session() as db:
        assert db.get(Faculty, FACULTY_ID).availability_reviewed_term_id is None
        review(db, FACULTY_ID, TERM_ID, [SLOT_ID])
        assert db.get(Faculty, FACULTY_ID).availability_reviewed_term_id == TERM_ID

    # Once the reader commits, the same real mutation must clear its new stamp.
    with make_session() as db:
        _change_grid(db, change)
    with make_session() as db:
        faculty = db.get(Faculty, FACULTY_ID)
        slots = db.scalars(select(TimeSlot).where(TimeSlot.term_id == TERM_ID)).all()
        assert faculty.availability_reviewed_term_id is None
        assert faculty.availability_mask == "1"
        assert len(slots) == {"create": 2, "replace": 1, "generate": 48}[change]
        if change == "replace":
            assert slots[0].time_slot_id != SLOT_ID


@pytest.mark.parametrize("operation", ["save", "confirm"])
def test_availability_waits_for_replacement_and_rejects_the_old_selection(
    committed_grid, operation,
):
    make_session = committed_grid
    ready = Event()
    pids = []

    def review_old_grid():
        with make_session() as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            review = availability.set_availability if operation == "save" else availability.confirm_availability
            review(db, FACULTY_ID, TERM_ID, [SLOT_ID])

    with make_session() as owner:
        owner.scalar(select(Term.term_id).where(Term.term_id == TERM_ID).with_for_update())
        owner_pid = owner.scalar(text("SELECT pg_backend_pid()"))
        owner.delete(owner.get(TimeSlot, SLOT_ID))
        owner.flush()
        owner.add(TimeSlot(
            term_id=TERM_ID, label="<REPLACEMENT-WINDOW>",
            start_time=time(12), end_time=time(13, 30), ordinal=0,
        ))
        owner.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(review_old_grid)
            try:
                assert ready.wait(5)
                _wait_for_blocker(make_session, owner_pid, pids[0], future)
                owner.commit()
                with pytest.raises(APIError) as refused:
                    future.result(timeout=10)
                assert refused.value.code == (
                    "INVALID_AVAILABILITY_MASK" if operation == "save" else "AVAILABILITY_REVIEW_STALE"
                )
            finally:
                owner.rollback()
    with make_session() as db:
        faculty = db.get(Faculty, FACULTY_ID)
        assert faculty.availability_reviewed_term_id is None
        assert faculty.availability_mask == "1"


def test_confirmation_locks_term_before_faculty(committed_grid):
    make_session = committed_grid
    ready = Event()
    pids = []
    with make_session() as db:
        db.get(Faculty, FACULTY_ID).availability_reviewed_term_id = TERM_ID
        db.commit()

    def confirm():
        with make_session() as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            availability.confirm_availability(db, FACULTY_ID, TERM_ID, [SLOT_ID])

    with make_session() as owner:
        owner.scalar(select(Term.term_id).where(Term.term_id == TERM_ID).with_for_update())
        owner_pid = owner.scalar(text("SELECT pg_backend_pid()"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(confirm)
            try:
                assert ready.wait(5)
                _wait_for_blocker(make_session, owner_pid, pids[0], future)
                # Invalidation must not wait on a Faculty lock held by this reader.
                assert owner.scalar(
                    select(Faculty.faculty_id).where(Faculty.faculty_id == FACULTY_ID)
                    .with_for_update(nowait=True)
                ) == FACULTY_ID
            finally:
                owner.rollback()
            future.result(timeout=10)


def test_grid_lock_does_not_block_term_foreign_key_checks(committed_grid):
    make_session = committed_grid
    with make_session() as owner:
        time_slots.ensure_canonical_slots(owner, TERM_ID)
        with make_session() as other:
            other.execute(text("SET LOCAL lock_timeout = '500ms'"))
            other.add(Schedule(term_id=TERM_ID))
            other.flush()
