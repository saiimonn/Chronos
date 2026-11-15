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
from app.domain.meeting_patterns import decode_preferred_patterns
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    Room,
    Subject,
    SubjectOffering,
    Term,
    TimeSlot,
)


def map_term(term: Term) -> TermDTO:
    return TermDTO(
        term_id=term.term_id,
        academic_year=term.academic_year,
        term_number=term.term_number,
        is_active=term.is_active,
    )


def map_faculty(faculty: Faculty) -> FacultyDTO:
    return FacultyDTO(
        faculty_id=faculty.faculty_id,
        full_name=faculty.full_name,
        is_full_time=faculty.is_full_time,
        availability_mask=faculty.availability_mask,
        # None only on a row not yet flushed, whose server default is active.
        is_active=faculty.is_active is not False,
        preferred_subjects=frozenset(faculty.preferred_subject_codes),
        non_preferred_subjects=frozenset(faculty.non_preferred_subject_codes),
        lecture_only=bool(faculty.lecture_only),
        # None only on a row not yet flushed, whose server default is empty.
        day_availability_masks=faculty.day_availability_masks or "",
    )


def map_subject(subject: Subject) -> SubjectDTO:
    return SubjectDTO(
        subject_code=subject.subject_code,
        subject_title=subject.subject_title,
        units=subject.units,
        subject_type=SubjectType(subject.subject_type),
        # None only on a row not yet flushed, whose server default is active.
        is_active=subject.is_active is not False,
        preferred_patterns=decode_preferred_patterns(subject.preferred_day_patterns),
        allowed_rooms=frozenset(subject.allowed_room_codes),
    )


def map_room(room: Room) -> RoomDTO:
    return RoomDTO(
        room_code=room.room_code,
        building_name=room.building_name,
        capacity=room.capacity,
        room_type=RoomType(room.room_type),
        expanded=room.expanded,
        is_active=room.is_active is not False,
    )


def map_program(program: CourseProgram) -> CourseProgramDTO:
    return CourseProgramDTO(
        program_code=program.program_code,
        program_name=program.program_name,
        is_active=program.is_active is not False,
    )


def map_block_class(link: BlockSectionOffering) -> BlockClassDTO:
    """Flatten a Block, its offering, and the link between them into one class.

    Capacity comes from the Block rather than from the link: it is derived from
    the Block's expansion flag and is the same for every subject that Block
    takes.
    """
    return BlockClassDTO(
        class_id=link.class_id,
        block_id=link.block_id,
        term_id=link.term_id,
        offering_id=link.offering_id,
        group_no=link.offering.group_no,
        subject_code=link.subject_code,
        program_code=link.block.program_code,
        year_level=link.block.year_level,
        section_letter=link.block.section_letter,
        capacity=link.block.capacity,
    )


def map_time_slot(time_slot: TimeSlot) -> TimeSlotDTO:
    return TimeSlotDTO(
        time_slot_id=time_slot.time_slot_id,
        term_id=time_slot.term_id,
        label=time_slot.label,
        start_time=time_slot.start_time,
        end_time=time_slot.end_time,
        ordinal=time_slot.ordinal,
    )


def map_subject_offering(offering: SubjectOffering) -> SubjectOfferingDTO:
    """Map a SubjectOffering ORM row to a detached DTO."""
    return SubjectOfferingDTO(
        offering_id=offering.offering_id,
        term_id=offering.term_id,
        subject_code=offering.subject_code,
        group_no=offering.group_no,
    )


def map_block_section(block: BlockSection) -> BlockSectionDTO:
    """Map a BlockSection ORM row to a detached DTO."""
    return BlockSectionDTO(
        block_id=block.block_id,
        term_id=block.term_id,
        program_code=block.program_code,
        year_level=block.year_level,
        section_letter=block.section_letter,
        capacity=block.capacity,
    )
