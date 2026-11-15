from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.domain import (
    BlockGenerationContext,
    LockedAssignment,
    SchedulingProblem,
    SubjectType,
    build_lab_partners,
    build_offering_block_ids,
    build_offering_enrollment,
)
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Faculty,
    Room,
    Schedule,
    ScheduleRecord,
    Subject,
    Term,
    TimeSlot,
)
from app.repositories.mappers import (
    map_block_class,
    map_block_section,
    map_faculty,
    map_program,
    map_room,
    map_subject,
    map_term,
    map_time_slot,
)
from app.repositories.protocols import (
    BaseScheduleNotFoundError,
    BlockGenerationContextNotFoundError,
    SchedulingProblemNotFoundError,
    SchedulingProblemProvider,
)


class SQLAlchemySchedulingProblemProvider:
    """Build a detached current-term problem from normalized persistence."""

    def __init__(self, session: Session):
        self._session = session

    def get_problem(self, term_id: UUID) -> SchedulingProblem:
        term = self._session.get(Term, term_id)
        if term is None:
            raise SchedulingProblemNotFoundError(term_id)

        # One row per class: the Block, the offering it takes, and the link
        # that joins them. The Block is joined rather than lazily loaded because
        # the ordering and every mapped field read it.
        classes = self._session.scalars(
            select(BlockSectionOffering)
            .join(
                BlockSection,
                BlockSection.block_id == BlockSectionOffering.block_id,
            )
            .where(BlockSectionOffering.term_id == term_id)
            .order_by(
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                BlockSectionOffering.subject_code,
                BlockSectionOffering.class_id,
            )
            .options(
                joinedload(BlockSectionOffering.block),
                joinedload(BlockSectionOffering.offering),
            )
        ).all()

        subject_codes = sorted({item.subject_code for item in classes})
        program_codes = sorted({item.block.program_code for item in classes})
        subjects = self._load_referenced(
            Subject, Subject.subject_code, subject_codes, Subject.subject_code
        )
        programs = self._load_referenced(
            CourseProgram,
            CourseProgram.program_code,
            program_codes,
            CourseProgram.program_code,
        )
        faculty = _load_all_faculty(self._session)
        rooms = self._session.scalars(select(Room).order_by(Room.room_code)).all()
        time_slots = self._session.scalars(
            select(TimeSlot)
            .where(TimeSlot.term_id == term_id)
            .order_by(TimeSlot.ordinal, TimeSlot.time_slot_id)
        ).all()

        mapped_classes = tuple(map_block_class(item) for item in classes)
        mapped_subjects = tuple(map_subject(item) for item in subjects)

        return SchedulingProblem(
            term=map_term(term),
            faculty=tuple(map_faculty(item) for item in faculty),
            subjects=mapped_subjects,
            rooms=tuple(map_room(item) for item in rooms),
            programs=tuple(map_program(item) for item in programs),
            classes=mapped_classes,
            time_slots=tuple(map_time_slot(item) for item in time_slots),
            offering_block_ids=build_offering_block_ids(mapped_classes),
            lab_partners=_lab_partners(mapped_classes, mapped_subjects),
        )

    def _load_referenced(
        self,
        model: type,
        identity_column,
        identities: Sequence,
        order_column,
    ) -> Sequence:
        if not identities:
            return ()
        return self._session.scalars(
            select(model)
            .where(identity_column.in_(identities))
            .order_by(order_column)
        ).all()


def _lab_partners(mapped_classes, mapped_subjects) -> dict[UUID, UUID]:
    return build_lab_partners(
        mapped_classes,
        {s.subject_code for s in mapped_subjects if s.subject_type is SubjectType.LAB},
    )


def _load_all_faculty(session: Session) -> Sequence[Faculty]:
    # Inactive members too, so any schedule that names a retired teacher still
    # validates, exports and captures. FacultyDTO.is_active keeps them out of
    # the GA's candidate pools and readiness.
    return session.scalars(select(Faculty).order_by(Faculty.faculty_id)).all()


class SQLAlchemyBlockGenerationContextProvider:
    """Build a detached per-Block generation context from normalized persistence."""

    def __init__(self, session: Session):
        self._session = session

    def get_block_generation_context(
        self,
        term_id: UUID,
        block_id: UUID,
        base_schedule_id: UUID | None = None,
    ) -> BlockGenerationContext:
        term = self._session.get(Term, term_id)
        if term is None:
            raise BlockGenerationContextNotFoundError(term_id, block_id)

        block = self._session.get(BlockSection, block_id)
        if block is None or block.term_id != term_id:
            raise BlockGenerationContextNotFoundError(term_id, block_id)

        all_classes = self._session.scalars(
            select(BlockSectionOffering)
            .join(
                BlockSection,
                BlockSection.block_id == BlockSectionOffering.block_id,
            )
            .where(BlockSectionOffering.term_id == term_id)
            .order_by(
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                BlockSectionOffering.subject_code,
                BlockSectionOffering.class_id,
            )
            .options(
                joinedload(BlockSectionOffering.block),
                joinedload(BlockSectionOffering.offering),
            )
        ).all()

        selected_classes = self._session.scalars(
            select(BlockSectionOffering)
            .where(
                BlockSectionOffering.term_id == term_id,
                BlockSectionOffering.block_id == block_id,
            )
            .order_by(
                BlockSectionOffering.subject_code,
                BlockSectionOffering.class_id,
            )
            .options(
                joinedload(BlockSectionOffering.block),
                joinedload(BlockSectionOffering.offering),
            )
        ).all()

        subject_codes = sorted({item.subject_code for item in all_classes})
        program_codes = sorted({item.block.program_code for item in all_classes})
        subjects = self._load_referenced(
            Subject, Subject.subject_code, subject_codes, Subject.subject_code
        )
        programs = self._load_referenced(
            CourseProgram,
            CourseProgram.program_code,
            program_codes,
            CourseProgram.program_code,
        )
        locked_assignments = self._load_locked_assignments(
            term_id,
            block_id,
            base_schedule_id,
            all_classes,
            {subject.subject_code: subject.units for subject in subjects},
        )
        faculty = _load_all_faculty(self._session)
        rooms = self._session.scalars(select(Room).order_by(Room.room_code)).all()
        time_slots = self._session.scalars(
            select(TimeSlot)
            .where(TimeSlot.term_id == term_id)
            .order_by(TimeSlot.ordinal, TimeSlot.time_slot_id)
        ).all()

        mapped_selected_classes = tuple(map_block_class(item) for item in selected_classes)
        mapped_all_classes = tuple(map_block_class(item) for item in all_classes)
        mapped_subjects = tuple(map_subject(item) for item in subjects)

        return BlockGenerationContext(
            term=map_term(term),
            selected_block_id=block_id,
            selected_block=map_block_section(block),
            selected_classes=mapped_selected_classes,
            locked_assignments=locked_assignments,
            faculty=tuple(map_faculty(item) for item in faculty),
            subjects=mapped_subjects,
            rooms=tuple(map_room(item) for item in rooms),
            programs=tuple(map_program(item) for item in programs),
            time_slots=tuple(map_time_slot(item) for item in time_slots),
            offering_block_ids=build_offering_block_ids(mapped_all_classes),
            base_schedule_id=base_schedule_id,
            offering_enrollment=build_offering_enrollment(mapped_all_classes),
            lab_partners=_lab_partners(mapped_all_classes, mapped_subjects),
        )

    def _load_locked_assignments(
        self,
        term_id: UUID,
        block_id: UUID,
        base_schedule_id: UUID | None,
        term_classes: Sequence[BlockSectionOffering],
        units_by_subject: Mapping[str, int],
    ) -> tuple[LockedAssignment, ...]:
        """Collect the immutable assignments held by every non-selected Block.

        A base Schedule that is missing or belongs to another Term is an error
        rather than an empty result: silently dropping the locks would let the
        GA place the selected Block on top of assignments it cannot see.
        """
        if base_schedule_id is None:
            return ()

        schedule = self._session.get(Schedule, base_schedule_id)
        if schedule is None or schedule.term_id != term_id:
            raise BaseScheduleNotFoundError(term_id, base_schedule_id)

        classes_by_id = {item.class_id: item for item in term_classes}
        records = self._session.scalars(
            select(ScheduleRecord)
            .where(
                ScheduleRecord.schedule_id == base_schedule_id,
                ScheduleRecord.term_id == term_id,
                ScheduleRecord.is_historical.is_(False),
                ScheduleRecord.class_id.isnot(None),
                ScheduleRecord.time_slot_id.isnot(None),
            )
            .order_by(ScheduleRecord.class_id, ScheduleRecord.record_id)
        ).all()

        locked_assignments: list[LockedAssignment] = []
        for record in records:
            if (
                record.class_id is None
                or record.time_slot_id is None
                or not record.meeting_days
            ):
                continue
            locked_class = classes_by_id.get(record.class_id)
            if locked_class is None or locked_class.block_id == block_id:
                continue
            locked_assignments.append(
                LockedAssignment(
                    class_id=record.class_id,
                    faculty_id=record.faculty_id,
                    time_slot_id=record.time_slot_id,
                    room_code=record.room_code or "",
                    meeting_days=record.meeting_days or "",
                    block_id=locked_class.block_id,
                    offering_id=locked_class.offering_id,
                    units=units_by_subject[locked_class.subject_code],
                )
            )
        return tuple(locked_assignments)

    def _load_referenced(
        self,
        model: type,
        identity_column,
        identities: Sequence,
        order_column,
    ) -> Sequence:
        if not identities:
            return ()
        return self._session.scalars(
            select(model)
            .where(identity_column.in_(identities))
            .order_by(order_column)
        ).all()


__all__ = [
    "BaseScheduleNotFoundError",
    "BlockGenerationContextNotFoundError",
    "SchedulingProblemNotFoundError",
    "SchedulingProblemProvider",
    "SQLAlchemyBlockGenerationContextProvider",
    "SQLAlchemySchedulingProblemProvider",
]
