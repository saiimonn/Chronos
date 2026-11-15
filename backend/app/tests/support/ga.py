"""Scheduling problem with enough variety to exercise bounded GA operators."""

from dataclasses import replace
from datetime import time
from uuid import UUID

from app.domain import (
    BlockClassDTO,
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

GA_TERM_ID = UUID("00000000-0000-0000-0000-0000000000a0")
FACULTY_FLEXIBLE_ID = UUID("00000000-0000-0000-0000-0000000000a1")
FACULTY_MORNING_ID = UUID("00000000-0000-0000-0000-0000000000a2")

# Offering-level identifiers (used by GA operators and genes).
OFFERING_LECTURE_ID = UUID("00000000-0000-0000-0000-0000000000d1")
OFFERING_LAB_ID = UUID("00000000-0000-0000-0000-0000000000d2")
OFFERING_UNSTAFFABLE_ID = UUID("00000000-0000-0000-0000-0000000000d3")

# Block-level identifiers (class_ids — used by the constraint engine).
SECTION_LECTURE_ID = UUID("00000000-0000-0000-0000-0000000000a3")
SECTION_LAB_ID = UUID("00000000-0000-0000-0000-0000000000a4")
SECTION_UNSTAFFABLE_ID = UUID("00000000-0000-0000-0000-0000000000a5")

SLOT_MORNING_ID = UUID("00000000-0000-0000-0000-0000000000a6")
SLOT_AFTERNOON_ID = UUID("00000000-0000-0000-0000-0000000000a7")
SLOT_LAB_ID = UUID("00000000-0000-0000-0000-0000000000a8")

LECTURE_ROOM = "<ROOM-LECTURE>"
LABORATORY_ROOM = "<ROOM-LABORATORY>"
SMALL_ROOM = "<ROOM-SMALL>"

LECTURE_SUBJECT = "<SUBJECT-LECTURE>"
LAB_SUBJECT = "<SUBJECT-LAB>"
UNSTAFFED_SUBJECT = "<SUBJECT-UNSTAFFED>"


def _lecture_section() -> BlockClassDTO:
    return BlockClassDTO(
        class_id=SECTION_LECTURE_ID,
        block_id=UUID("00000000-0000-0000-0000-0000000000c1"),
        term_id=GA_TERM_ID,
        offering_id=OFFERING_LECTURE_ID,
        group_no=1,
        subject_code=LECTURE_SUBJECT,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="A",
        capacity=25,
    )


def _lab_section() -> BlockClassDTO:
    return BlockClassDTO(
        class_id=SECTION_LAB_ID,
        block_id=UUID("00000000-0000-0000-0000-0000000000c2"),
        term_id=GA_TERM_ID,
        offering_id=OFFERING_LAB_ID,
        group_no=1,
        subject_code=LAB_SUBJECT,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="B",
        capacity=25,
    )


def _unstaffable_section() -> BlockClassDTO:
    return BlockClassDTO(
        class_id=SECTION_UNSTAFFABLE_ID,
        block_id=UUID("00000000-0000-0000-0000-0000000000c3"),
        term_id=GA_TERM_ID,
        offering_id=OFFERING_UNSTAFFABLE_ID,
        group_no=1,
        subject_code=UNSTAFFED_SUBJECT,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="C",
        capacity=50,
    )


def make_ga_problem(*, include_unstaffable: bool = True) -> SchedulingProblem:
    """Sections: one broadly placeable, one lab-only, one unplaceable.

    Dropping the unstaffable section leaves a problem that a complete,
    conflict-free schedule exists for, which is what makes early optimal
    termination observable.
    """

    classes = (
        _lecture_section(),
        _lab_section(),
    ) + ((_unstaffable_section(),) if include_unstaffable else ())

    return SchedulingProblem(
        term=TermDTO(
            term_id=GA_TERM_ID,
            academic_year="<YEAR-1>",
            term_number=1,
            is_active=True,
        ),
        faculty=(
            FacultyDTO(
                faculty_id=FACULTY_FLEXIBLE_ID,
                full_name="<FACULTY-FLEXIBLE>",
                is_full_time=False,
                availability_mask="111",
            ),
            FacultyDTO(
                faculty_id=FACULTY_MORNING_ID,
                full_name="<FACULTY-MORNING>",
                is_full_time=False,
                availability_mask="100",
            ),
        ),
        subjects=(
            SubjectDTO(
                subject_code=LECTURE_SUBJECT,
                subject_title="<SUBJECT-TITLE-LECTURE>",
                units=3,
                subject_type=SubjectType.LEC,
            ),
            SubjectDTO(
                subject_code=LAB_SUBJECT,
                subject_title="<SUBJECT-TITLE-LAB>",
                units=3,
                subject_type=SubjectType.LAB,
            ),
            SubjectDTO(
                subject_code=UNSTAFFED_SUBJECT,
                subject_title="<SUBJECT-TITLE-UNSTAFFED>",
                units=3,
                subject_type=SubjectType.LEC,
            ),
        ),
        rooms=(
            RoomDTO(
                room_code=LECTURE_ROOM,
                building_name="<BUILDING-1>",
                capacity=40,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
            RoomDTO(
                room_code=LABORATORY_ROOM,
                building_name="<BUILDING-1>",
                capacity=25,
                room_type=RoomType.LABORATORY,
                expanded=True,
            ),
            RoomDTO(
                room_code=SMALL_ROOM,
                building_name="<BUILDING-1>",
                capacity=10,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
        ),
        programs=(
            CourseProgramDTO(
                program_code="<PROGRAM-1>",
                program_name="<PROGRAM-NAME-1>",
            ),
        ),
        classes=classes,
        time_slots=(
            TimeSlotDTO(
                time_slot_id=SLOT_MORNING_ID,
                term_id=GA_TERM_ID,
                label="<TIME-SLOT-MORNING>",
                start_time=time(8),
                end_time=time(9, 30),
                ordinal=0,
            ),
            TimeSlotDTO(
                time_slot_id=SLOT_AFTERNOON_ID,
                term_id=GA_TERM_ID,
                label="<TIME-SLOT-AFTERNOON>",
                start_time=time(13),
                end_time=time(14, 30),
                ordinal=1,
            ),
            TimeSlotDTO(
                time_slot_id=SLOT_LAB_ID,
                term_id=GA_TERM_ID,
                label="<TIME-SLOT-LAB>",
                start_time=time(15),
                end_time=time(17, 30),
                ordinal=2,
            ),
        ),
        offering_block_ids=build_offering_block_ids(classes),
    )


COMBINED_CLASS_A_ID = UUID("00000000-0000-0000-0000-0000000000a9")
COMBINED_CLASS_B_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def make_combined_offering_problem() -> SchedulingProblem:
    """One lecture offering combining two Blocks into a single session.

    This is the shape the offering-level gene exists for: the two Blocks are
    scheduled together, so they share a faculty member, a room, and a slot,
    and the room has to seat both cohorts at once.
    """

    problem = make_ga_problem(include_unstaffable=False)
    classes = tuple(
        BlockClassDTO(
            class_id=class_id,
            block_id=block_id,
            term_id=GA_TERM_ID,
            offering_id=OFFERING_LECTURE_ID,
            group_no=1,
            subject_code=LECTURE_SUBJECT,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter=letter,
            capacity=20,
        )
        for class_id, block_id, letter in (
            (
                COMBINED_CLASS_A_ID,
                UUID("00000000-0000-0000-0000-0000000000c4"),
                "A",
            ),
            (
                COMBINED_CLASS_B_ID,
                UUID("00000000-0000-0000-0000-0000000000c5"),
                "B",
            ),
        )
    )
    return replace(
        problem,
        classes=classes,
        offering_block_ids=build_offering_block_ids(classes),
    )


CONGESTED_SECTION_LETTERS = ("A", "B", "C", "D", "E", "F")


def make_congested_ga_problem() -> SchedulingProblem:
    """Six lecture sections competing for two faculty, two slots, and two rooms.

    A complete conflict-free schedule exists but a random initial population
    will not hold one, so the search has to improve across generations.
    """

    problem = make_ga_problem(include_unstaffable=False)
    classes = tuple(
        BlockClassDTO(
            class_id=UUID(f"00000000-0000-0000-0000-0000000000b{index}"),
            block_id=UUID(f"00000000-0000-0000-0000-0000000000e{index}"),
            term_id=GA_TERM_ID,
            offering_id=UUID(f"00000000-0000-0000-0000-0000000000f{index}"),
            group_no=1,
            subject_code=LECTURE_SUBJECT,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter=letter,
            capacity=25,
        )
        for index, letter in enumerate(CONGESTED_SECTION_LETTERS)
    )
    flexible_faculty = tuple(
        replace(member, availability_mask="111") for member in problem.faculty
    )
    return replace(
        problem,
        faculty=flexible_faculty,
        classes=classes,
        # Lectures never take a laboratory, so both rooms are lecture rooms.
        rooms=tuple(
            replace(room, room_type=RoomType.LECTURE)
            for room in problem.rooms
            if room.capacity >= 25
        ),
        offering_block_ids=build_offering_block_ids(classes),
    )
