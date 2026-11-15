from datetime import time
from uuid import UUID

from app.domain import (
    BlockClassDTO,
    BlockSectionDTO,
    CourseProgramDTO,
    FacultyDTO,
    RoomDTO,
    RoomType,
    SubjectDTO,
    SubjectOfferingDTO,
    SubjectType,
    TermDTO,
    TimeSlotDTO,
)
from app.models import (
    CourseProgram,
    BlockSection,
    BlockSectionOffering,
    SubjectOffering,
    Faculty,
    Room,
    Subject,
    Term,
    TimeSlot,
)
from app.repositories.mappers import (
    map_block_section,
    map_faculty,
    map_program,
    map_room,
    map_block_class,
    map_subject,
    map_subject_offering,
    map_term,
    map_time_slot,
)

TERM_ID = UUID("10000000-0000-0000-0000-000000000001")
FACULTY_ID = UUID("10000000-0000-0000-0000-000000000002")
CLASS_ID = UUID("10000000-0000-0000-0000-000000000003")
BLOCK_ID = UUID("10000000-0000-0000-0000-000000000006")
OFFERING_ID = UUID("10000000-0000-0000-0000-000000000007")
TIME_SLOT_ID = UUID("10000000-0000-0000-0000-000000000004")


def test_orm_rows_map_to_detached_domain_values():
    term = Term(
        term_id=TERM_ID,
        academic_year="<YEAR-1>",
        term_number=1,
        is_active=True,
    )
    faculty = Faculty(
        faculty_id=FACULTY_ID,
        full_name="<FACULTY>",
        is_full_time=False,
        availability_mask="101",
    )
    subject = Subject(
        subject_code="<SUBJECT>",
        subject_title="<SUBJECT-TITLE>",
        units=3,
        subject_type=SubjectType.LEC,
    )
    room = Room(
        room_code="<ROOM>",
        building_name="<BUILDING>",
        capacity=40,
        room_type="lecture",
        expanded=False,
    )
    program = CourseProgram(
        program_code="<PROGRAM>", program_name="<PROGRAM-NAME>"
    )
    block = BlockSection(
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        program_code=program.program_code,
        year_level=1,
        section_letter="A",
        is_expanded=True,
    )
    offering = SubjectOffering(
        offering_id=OFFERING_ID,
        term_id=TERM_ID,
        subject_code=subject.subject_code,
        group_no=2,
    )
    block_class = BlockSectionOffering(
        class_id=CLASS_ID,
        block_id=BLOCK_ID,
        offering_id=OFFERING_ID,
        term_id=TERM_ID,
        subject_code=subject.subject_code,
    )
    block_class.block = block
    block_class.offering = offering
    time_slot = TimeSlot(
        time_slot_id=TIME_SLOT_ID,
        term_id=TERM_ID,
        label="<TIME-SLOT>",
        start_time=time(8),
        end_time=time(9),
        ordinal=0,
    )

    assert map_term(term) == TermDTO(
        TERM_ID, "<YEAR-1>", 1, True
    )
    assert map_faculty(faculty) == FacultyDTO(
        FACULTY_ID, "<FACULTY>", False, "101"
    )
    assert map_subject(subject) == SubjectDTO(
        "<SUBJECT>", "<SUBJECT-TITLE>", 3, SubjectType.LEC
    )
    assert map_subject(subject).duration_minutes == 90
    assert map_room(room) == RoomDTO(
        "<ROOM>", "<BUILDING>", 40, RoomType.LECTURE, False
    )
    assert map_program(program) == CourseProgramDTO(
        "<PROGRAM>", "<PROGRAM-NAME>"
    )
    assert map_block_class(block_class) == BlockClassDTO(
        class_id=CLASS_ID,
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        offering_id=OFFERING_ID,
        group_no=2,
        subject_code="<SUBJECT>",
        program_code="<PROGRAM>",
        year_level=1,
        section_letter="A",
        capacity=25,
    )
    assert map_time_slot(time_slot) == TimeSlotDTO(
        TIME_SLOT_ID, TERM_ID, "<TIME-SLOT>", time(8), time(9), 0
    )


def test_maps_a_subject_offering_to_a_detached_dto() -> None:
    offering = SubjectOffering(
        offering_id=OFFERING_ID,
        term_id=TERM_ID,
        subject_code="<SUBJECT>",
        group_no=2,
    )

    assert map_subject_offering(offering) == SubjectOfferingDTO(
        offering_id=OFFERING_ID,
        term_id=TERM_ID,
        subject_code="<SUBJECT>",
        group_no=2,
    )


def test_maps_a_block_section_to_a_detached_dto_with_derived_capacity() -> None:
    block = BlockSection(
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        program_code="<PROGRAM>",
        year_level=1,
        section_letter="A",
        is_expanded=True,
    )

    mapped = map_block_section(block)

    assert mapped == BlockSectionDTO(
        block_id=BLOCK_ID,
        term_id=TERM_ID,
        program_code="<PROGRAM>",
        year_level=1,
        section_letter="A",
        capacity=block.capacity,
    )
    assert mapped.capacity == 25
