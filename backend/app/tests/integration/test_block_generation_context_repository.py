"""CHR-89 / CHR-SCH-009 Block generation context repository tests."""

from datetime import time
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models import (
    BlockSection,
    BlockSectionOffering,
    Faculty,
    Room,
    Schedule,
    ScheduleRecord,
    Subject,
    SubjectOffering,
    TimeSlot,
)
from app.repositories import (
    BaseScheduleNotFoundError,
    BlockGenerationContextNotFoundError,
)
from app.repositories.scheduling import SQLAlchemyBlockGenerationContextProvider
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

OTHER_BLOCK_ID = UUID("00000000-0000-0000-0000-000000000020")
OTHER_CLASS_ID = UUID("00000000-0000-0000-0000-000000000021")
OTHER_OFFERING_ID = UUID("00000000-0000-0000-0000-000000000022")
THIRD_CLASS_ID = UUID("00000000-0000-0000-0000-000000000023")
THIRD_OFFERING_ID = UUID("00000000-0000-0000-0000-000000000024")
SECOND_SLOT_ID = UUID("00000000-0000-0000-0000-000000000025")
UNKNOWN_ID = UUID("00000000-0000-0000-0000-000000000099")


def seed_second_block(session: Session, *, ready_for_generation: bool = False) -> None:
    """Add a second Block carrying two classes, one per Subject in the term."""
    session.add(
        Subject(
            subject_code="<SUBJECT-2>",
            subject_title="<SUBJECT-TITLE-2>",
            units=3,
            subject_type="LAB",
        )
    )
    session.add(
        BlockSection(
            block_id=OTHER_BLOCK_ID,
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="B",
            is_expanded=False,
        )
    )
    session.flush()
    session.add_all(
        [
            SubjectOffering(
                offering_id=OTHER_OFFERING_ID,
                term_id=TERM_ID,
                subject_code="<SUBJECT-1>",
                group_no=2,
            ),
            SubjectOffering(
                offering_id=THIRD_OFFERING_ID,
                term_id=TERM_ID,
                subject_code="<SUBJECT-2>",
                group_no=1,
            ),
            TimeSlot(
                time_slot_id=SECOND_SLOT_ID,
                term_id=TERM_ID,
                label="<TIME-SLOT-2>",
                start_time=time(9, 30),
                end_time=time(11),
                ordinal=1,
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            BlockSectionOffering(
                class_id=OTHER_CLASS_ID,
                block_id=OTHER_BLOCK_ID,
                offering_id=OTHER_OFFERING_ID,
                term_id=TERM_ID,
                subject_code="<SUBJECT-1>",
            ),
            BlockSectionOffering(
                class_id=THIRD_CLASS_ID,
                block_id=OTHER_BLOCK_ID,
                offering_id=THIRD_OFFERING_ID,
                term_id=TERM_ID,
                subject_code="<SUBJECT-2>",
            ),
        ]
    )
    session.flush()

    if ready_for_generation:
        session.add_all([
            Room(
                room_code="<ROOM-LAB>", building_name="<BUILDING-1>",
                capacity=40, room_type="laboratory", expanded=False,
            ),
            TimeSlot(
                time_slot_id=UUID(int=124900), term_id=TERM_ID, label="<LAB-SLOT>",
                start_time=time(12), end_time=time(14, 30), ordinal=2,
            ),
        ])
        session.get(Faculty, FACULTY_ID).availability_mask = "111"
        session.flush()


def seed_base_schedule(session: Session) -> None:
    """Place every class in the term on a routine Schedule."""
    session.add(
        Schedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            schedule_type="routine",
        )
    )
    session.flush()
    session.add_all(
        [
            ScheduleRecord(
                schedule_id=SCHEDULE_ID,
                faculty_id=FACULTY_ID,
                subject_code=subject_code,
                term_id=TERM_ID,
                class_id=class_id,
                time_slot_id=time_slot_id,
                room_code="<ROOM-1>",
                meeting_days="MWF",
                is_historical=False,
            )
            for class_id, subject_code, time_slot_id in (
                (CLASS_ID, "<SUBJECT-1>", TIME_SLOT_ID),
                (OTHER_CLASS_ID, "<SUBJECT-1>", SECOND_SLOT_ID),
                (THIRD_CLASS_ID, "<SUBJECT-2>", TIME_SLOT_ID),
            )
        ]
    )
    session.flush()


def test_context_holds_selected_classes_and_other_block_locks(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    seed_base_schedule(db_session)

    context = SQLAlchemyBlockGenerationContextProvider(
        db_session
    ).get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)

    assert context.selected_block_id == BLOCK_ID
    assert context.selected_block.section_letter == "A"
    assert [item.class_id for item in context.selected_classes] == [CLASS_ID]
    assert [locked.class_id for locked in context.locked_assignments] == [
        OTHER_CLASS_ID,
        THIRD_CLASS_ID,
    ]
    assert all(
        locked.block_id == OTHER_BLOCK_ID for locked in context.locked_assignments
    )
    assert [
        (locked.offering_id, locked.units) for locked in context.locked_assignments
    ] == [(OTHER_OFFERING_ID, 3), (THIRD_OFFERING_ID, 3)]
    assert context.locked_assignments[0].room_code == "<ROOM-1>"
    assert context.locked_assignments[0].meeting_days == "MWF"
    assert context.base_schedule_id == SCHEDULE_ID
    # Cross-Block awareness: the map covers every offering in the term, not
    # only the ones the selected Block schedules.
    assert set(context.offering_block_ids) == {
        UUID("00000000-0000-0000-0000-000000000007"),
        OTHER_OFFERING_ID,
        THIRD_OFFERING_ID,
    }


def test_context_ordering_is_stable_across_calls(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    seed_base_schedule(db_session)
    provider = SQLAlchemyBlockGenerationContextProvider(db_session)

    first = provider.get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)
    db_session.expunge_all()
    second = provider.get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)

    assert first == second


def test_context_detaches_from_the_session(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    seed_base_schedule(db_session)

    context = SQLAlchemyBlockGenerationContextProvider(
        db_session
    ).get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)
    db_session.expunge_all()

    assert context.selected_classes[0].subject_code == "<SUBJECT-1>"
    assert context.term.term_id == TERM_ID
    assert [slot.ordinal for slot in context.time_slots] == [0, 1]


def test_context_without_base_schedule_has_no_locks(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    seed_base_schedule(db_session)

    context = SQLAlchemyBlockGenerationContextProvider(
        db_session
    ).get_block_generation_context(TERM_ID, BLOCK_ID)

    assert context.locked_assignments == ()
    assert context.base_schedule_id is None


def test_context_ignores_historical_and_unplaced_records(db_session: Session):
    seed_scheduling_problem(db_session)
    seed_second_block(db_session)
    seed_base_schedule(db_session)
    db_session.add_all(
        [
            # A past offering mined by ARM, not part of this Schedule.
            ScheduleRecord(
                faculty_id=FACULTY_ID,
                subject_code="<SUBJECT-1>",
                term_id=TERM_ID,
                is_historical=True,
            ),
            # A row the GA has not placed yet: no Schedule, no slot.
            ScheduleRecord(
                faculty_id=FACULTY_ID,
                subject_code="<SUBJECT-1>",
                term_id=TERM_ID,
                is_historical=False,
            ),
        ]
    )
    db_session.flush()

    context = SQLAlchemyBlockGenerationContextProvider(
        db_session
    ).get_block_generation_context(TERM_ID, BLOCK_ID, SCHEDULE_ID)

    assert [locked.class_id for locked in context.locked_assignments] == [
        OTHER_CLASS_ID,
        THIRD_CLASS_ID,
    ]


def test_context_rejects_unknown_term(db_session: Session):
    seed_scheduling_problem(db_session)

    with pytest.raises(BlockGenerationContextNotFoundError) as exc_info:
        SQLAlchemyBlockGenerationContextProvider(
            db_session
        ).get_block_generation_context(UNKNOWN_ID, BLOCK_ID)

    assert exc_info.value.term_id == UNKNOWN_ID


def test_context_rejects_unknown_block(db_session: Session):
    seed_scheduling_problem(db_session)

    with pytest.raises(BlockGenerationContextNotFoundError) as exc_info:
        SQLAlchemyBlockGenerationContextProvider(
            db_session
        ).get_block_generation_context(TERM_ID, UNKNOWN_ID)

    assert exc_info.value.block_id == UNKNOWN_ID


def test_context_rejects_unknown_base_schedule(db_session: Session):
    seed_scheduling_problem(db_session)

    with pytest.raises(BaseScheduleNotFoundError) as exc_info:
        SQLAlchemyBlockGenerationContextProvider(
            db_session
        ).get_block_generation_context(TERM_ID, BLOCK_ID, UNKNOWN_ID)

    assert exc_info.value.schedule_id == UNKNOWN_ID
