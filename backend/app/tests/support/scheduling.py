from dataclasses import asdict
from datetime import time
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain import (
    Assignment,
    BlockClassDTO,
    CandidateSchedule,
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
from app.domain.meeting_patterns import encode_preferred_patterns
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
from app.repositories import SchedulingProblemNotFoundError

TERM_ID = UUID("00000000-0000-0000-0000-000000000001")
FACULTY_ID = UUID("00000000-0000-0000-0000-000000000002")
CLASS_ID = UUID("00000000-0000-0000-0000-000000000003")
TIME_SLOT_ID = UUID("00000000-0000-0000-0000-000000000004")
SCHEDULE_ID = UUID("00000000-0000-0000-0000-000000000005")
BLOCK_ID = UUID("00000000-0000-0000-0000-000000000006")
OFFERING_ID = UUID("00000000-0000-0000-0000-000000000007")
OTHER_CLASS_ID = UUID("00000000-0000-0000-0000-0000000000c2")


def make_scheduling_problem() -> SchedulingProblem:
    classes = (
        BlockClassDTO(
            class_id=CLASS_ID,
            block_id=BLOCK_ID,
            term_id=TERM_ID,
            offering_id=OFFERING_ID,
            group_no=1,
            subject_code="<SUBJECT-1>",
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
    )
    return SchedulingProblem(
        term=TermDTO(
            term_id=TERM_ID,
            academic_year="<YEAR-1>",
            term_number=1,
            is_active=True,
        ),
        faculty=(
            FacultyDTO(
                faculty_id=FACULTY_ID,
                full_name="<FACULTY-1>",
                is_full_time=False,
                availability_mask="1",
            ),
        ),
        subjects=(
            SubjectDTO(
                subject_code="<SUBJECT-1>",
                subject_title="<SUBJECT-TITLE-1>",
                units=3,
                subject_type=SubjectType.LEC,
            ),
        ),
        rooms=(
            RoomDTO(
                room_code="<ROOM-1>",
                building_name="<BUILDING-1>",
                capacity=40,
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
                time_slot_id=TIME_SLOT_ID,
                term_id=TERM_ID,
                label="<TIME-SLOT-1>",
                start_time=time(8),
                end_time=time(9, 30),
                ordinal=0,
            ),
        ),
        offering_block_ids=build_offering_block_ids(classes),
    )


def _subject_row(subject: SubjectDTO) -> Subject:
    """The ORM row for a subject DTO; the preference is stored encoded."""
    values = asdict(subject)
    values["preferred_day_patterns"] = encode_preferred_patterns(
        values.pop("preferred_patterns")
    )
    values.pop("allowed_rooms")
    return Subject(**values)


def make_candidate_schedule() -> CandidateSchedule:
    return CandidateSchedule(
        schedule_id=SCHEDULE_ID,
        term_id=TERM_ID,
        assignments=(
            Assignment(
                class_id=CLASS_ID,
                faculty_id=FACULTY_ID,
                time_slot_id=TIME_SLOT_ID,
                room_code="<ROOM-1>",
                meeting_days="<DAYS-1>",
            ),
        ),
    )


class FixtureSchedulingProblemProvider:
    def __init__(self, problem: SchedulingProblem | None = None):
        self._problem = problem or make_scheduling_problem()

    def get_problem(self, term_id: UUID) -> SchedulingProblem:
        if term_id != self._problem.term.term_id:
            raise SchedulingProblemNotFoundError(term_id)
        return self._problem


def seed_scheduling_problem(session: Session) -> None:
    problem = make_scheduling_problem()
    session.add_all(
        [
            Term(
                term_id=problem.term.term_id,
                academic_year=problem.term.academic_year,
                term_number=problem.term.term_number,
                is_active=problem.term.is_active,
            ),
            # The subject lists are relationships of Subject rows on the
            # model, not the DTO's code sets; the fixture member has none.
            Faculty(**{
                key: value
                for key, value in asdict(problem.faculty[0]).items()
                if key not in ("preferred_subjects", "non_preferred_subjects")
            }),
            _subject_row(problem.subjects[0]),
            Room(**asdict(problem.rooms[0])),
            CourseProgram(**asdict(problem.programs[0])),
        ]
    )
    session.flush()
    # A seeded problem is meant to be ready, so its availability has already
    # been reviewed for this term (CHR-132). Set after the flush: the marker
    # references the term row.
    session.get(Faculty, problem.faculty[0].faculty_id).availability_reviewed_term_id = (
        problem.term.term_id
    )
    block_class = problem.classes[0]
    session.add_all(
        [
            BlockSection(
                block_id=block_class.block_id,
                term_id=block_class.term_id,
                program_code=block_class.program_code,
                year_level=block_class.year_level,
                section_letter=block_class.section_letter,
                is_expanded=False,
            ),
            SubjectOffering(
                offering_id=block_class.offering_id,
                term_id=block_class.term_id,
                subject_code=block_class.subject_code,
                group_no=block_class.group_no,
            ),
            TimeSlot(**asdict(problem.time_slots[0])),
        ]
    )
    session.flush()
    session.add(
        BlockSectionOffering(
            class_id=block_class.class_id,
            block_id=block_class.block_id,
            offering_id=block_class.offering_id,
            term_id=block_class.term_id,
            subject_code=block_class.subject_code,
        )
    )
    session.flush()


def add_combined_block(session: Session) -> None:
    """Seed a second Block taking OFFERING_ID, so CLASS_ID and OTHER_CLASS_ID
    attend one combined session."""
    other_block_id = UUID("00000000-0000-0000-0000-0000000000b2")
    session.add(
        BlockSection(
            block_id=other_block_id,
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="B",
            is_expanded=False,
        )
    )
    session.flush()
    first = session.get(BlockSectionOffering, CLASS_ID)
    session.add(
        BlockSectionOffering(
            class_id=OTHER_CLASS_ID,
            block_id=other_block_id,
            offering_id=OFFERING_ID,
            term_id=TERM_ID,
            subject_code=first.subject_code,
        )
    )
    session.flush()
