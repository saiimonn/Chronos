"""Historical snapshots for tests that submit ARM jobs.

An ARM submission is rejected unless every selected snapshot exists and the
selection survives the chronological holdout, so tests that expect a 202 need
real history rather than invented IDs.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course_program import CourseProgram
from app.models.curriculum import Curriculum, CurriculumRequirement
from app.models.faculty import Faculty
from app.models.historical_snapshot import HistoricalRecord, HistoricalSnapshot
from app.models.room import Room
from app.models.subject import Subject
from app.models.term import Term

# Four periods inside every stream's eligibility window. The holdout reserves
# the newest three, leaving exactly one training term.
DEFAULT_PERIODS = (
    ("2023-2024", 1),
    ("2023-2024", 2),
    ("2024-2025", 1),
    ("2024-2025", 2),
)


def add_to_curriculum(session: Session, *subject_codes: str) -> None:
    """Make `subject_codes` current offerings: ARM mines only history whose
    subject a reviewed curriculum still requires."""
    curriculum = session.scalars(
        select(Curriculum).where(Curriculum.program_code == "<CURRENT>")
    ).one_or_none()
    if curriculum is None:
        session.add(CourseProgram(program_code="<CURRENT>", program_name="<CURRENT>"))
        session.flush()
        curriculum = Curriculum(program_code="<CURRENT>", effective_year="2023-2024")
        session.add(curriculum)
        session.flush()
    for subject_code in subject_codes:
        session.add(
            CurriculumRequirement(
                curriculum_id=curriculum.curriculum_id,
                year_level=1,
                term_number=1,
                subject_code=subject_code,
            )
        )
    session.flush()


def seed_historical_terms(
    session: Session,
    periods: tuple[tuple[str, int], ...] = DEFAULT_PERIODS,
) -> list[UUID]:
    """Capture one instructor's repeated offering across `periods`.

    Returns the snapshot IDs, newest last.
    """
    faculty = Faculty(
        full_name="<ARM FACULTY>", is_full_time=False, availability_mask="1"
    )
    subject = Subject(
        subject_code="ARM101", subject_title="<ARM SUBJECT>", units=3, subject_type="LEC"
    )
    room = Room(
        room_code="ARMR1",
        building_name="<ARM BUILDING>",
        capacity=20,
        room_type="lecture",
        expanded=False,
    )
    session.add_all([faculty, subject, room])
    session.flush()
    add_to_curriculum(session, subject.subject_code)

    snapshot_ids: list[UUID] = []
    for academic_year, term_number in periods:
        term = session.scalars(
            select(Term).where(
                Term.academic_year == academic_year,
                Term.term_number == term_number,
            )
        ).one_or_none()
        if term is None:
            term = Term(
                academic_year=academic_year,
                term_number=term_number,
                is_active=False,
            )
            session.add(term)
            session.flush()

        snapshot = HistoricalSnapshot(
            source_term_id=term.term_id,
            academic_year=academic_year,
            term_number=term_number,
        )
        session.add(snapshot)
        session.flush()
        session.add(
            HistoricalRecord(
                snapshot_id=snapshot.snapshot_id,
                source_faculty_id=faculty.faculty_id,
                faculty_name=faculty.full_name,
                subject_code=subject.subject_code,
                subject_title=subject.subject_title,
                room_code=room.room_code,
                meeting_days="MWF",
                time_slot="07:30-08:30",
            )
        )
        snapshot_ids.append(snapshot.snapshot_id)
    session.flush()
    return snapshot_ids
