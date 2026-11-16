"""A paired LAB placed first leaves its partner a free teacher, not just a room."""

from dataclasses import replace
from datetime import time
from uuid import UUID

from app.domain import (
    BlockClassDTO,
    RoomDTO,
    RoomType,
    SubjectDTO,
    SubjectType,
    TimeSlotDTO,
    build_offering_block_ids,
)
from app.services.ga import GAConfig, GAEngine
from app.tests.support.ga import GA_TERM_ID, make_ga_problem

_EARLY = UUID(int=0x5101)
_LATE = UUID(int=0x5102)
_LAB = UUID(int=0x5201)
_PARTNER_LAB = UUID(int=0x5202)


def _problem():
    base = make_ga_problem(include_unstaffable=False)
    slots = (
        TimeSlotDTO(time_slot_id=_EARLY, term_id=GA_TERM_ID, label="LAB 08:00-10:30",
                    start_time=time(8), end_time=time(10, 30), ordinal=0),
        TimeSlotDTO(time_slot_id=_LATE, term_id=GA_TERM_ID, label="LAB 13:00-15:30",
                    start_time=time(13), end_time=time(15, 30), ordinal=1),
    )
    subject = SubjectDTO(subject_code="LAB-1", subject_title="x", units=3,
                         subject_type=SubjectType.LAB)
    classes = (
        BlockClassDTO(class_id=_LAB, block_id=UUID(int=0x5301), term_id=GA_TERM_ID,
                      offering_id=UUID(int=0x5401), group_no=1, subject_code="LAB-1",
                      program_code="<PROGRAM-1>", year_level=2, section_letter="C",
                      capacity=20),
    )
    rooms = tuple(
        RoomDTO(room_code=f"LAB-{n}", building_name="B", capacity=25,
                room_type=RoomType.LABORATORY, expanded=False)
        for n in (1, 2)
    )
    # Two teachers: the first free all day, the second only early. Whoever
    # takes this LAB late leaves the partner Block nobody at that time.
    flexible, morning = base.faculty
    faculty = (replace(flexible, availability_mask="11"),
               replace(morning, availability_mask="10"))
    return replace(
        base, time_slots=slots, subjects=(subject,), classes=classes, rooms=rooms,
        faculty=faculty, offering_block_ids=build_offering_block_ids(classes),
        # The partner Block (D) has not been generated yet.
        lab_partners={_LAB: _PARTNER_LAB},
    )


def test_a_paired_lab_takes_a_time_its_partner_can_also_staff():
    problem = _problem()
    for seed in range(8):
        result = GAEngine(
            problem,
            GAConfig(population_size=8, max_generations=5, tournament_size=2,
                     random_seed=seed, meeting_day_patterns=("MW", "TTh")),
            repair_initial_population=True,
        ).run()
        (assignment,) = result.candidate.assignments
        assert assignment.time_slot_id == _EARLY, seed
