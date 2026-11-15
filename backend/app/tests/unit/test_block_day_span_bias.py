"""Compact Block days: a soft sampler bias toward a 9h (10h with a break) day."""

from dataclasses import replace
from datetime import time
from random import Random
from uuid import UUID

from app.domain import TimeSlotDTO, build_offering_block_ids
from app.services.ga import GAConfig, GeneSampler
from app.services.ga.chromosome import ScheduleGene
from app.services.ga.operators import COMPACT_BLOCK_DAY_PROBABILITY, _compact_day
from app.tests.support.ga import (
    GA_TERM_ID,
    OFFERING_LECTURE_ID,
    SECTION_LECTURE_ID,
    make_ga_problem,
)

_CONFIG = GAConfig(
    population_size=8, max_generations=5, tournament_size=2, random_seed=1234
)
_OTHER_OFFERING_ID = UUID("00000000-0000-0000-0000-0000000000e1")
_OTHER_CLASS_ID = UUID("00000000-0000-0000-0000-0000000000e2")
# 07:30, 09:00, ... 19:30: nine 1.5h lecture slots.
_STARTS = [(7, 30), (9, 0), (10, 30), (12, 0), (13, 30), (15, 0), (16, 30), (18, 0), (19, 30)]


def _h(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def test_compact_day_allows_nine_hours_or_ten_with_a_break():
    assert _compact_day([(_h(7, 30), _h(9)), (_h(15), _h(16, 30))])
    assert _compact_day([(_h(10, 30), _h(12)), (_h(18), _h(19, 30))])
    # 07:30-17:30 with a 12:00-13:30 break.
    assert _compact_day([(_h(7, 30), _h(12)), (_h(13, 30), _h(17, 30))])
    # 07:30-17:30 back to back has no break.
    assert not _compact_day([(_h(7, 30), _h(12, 30)), (_h(12, 30), _h(17, 30))])
    assert not _compact_day([(_h(7, 30), _h(9)), (_h(18), _h(19, 30))])


def _wide_grid_problem():
    problem = make_ga_problem(include_unstaffable=False)
    lecture = next(c for c in problem.classes if c.class_id == SECTION_LECTURE_ID)
    other = replace(
        lecture, class_id=_OTHER_CLASS_ID, offering_id=_OTHER_OFFERING_ID
    )
    classes = (lecture, other)
    slots = tuple(
        TimeSlotDTO(
            time_slot_id=UUID(int=0xF00 + index),
            term_id=GA_TERM_ID,
            label=f"<SLOT-{index}>",
            start_time=time(hour, minute),
            end_time=time(hour + 1 + (minute + 30) // 60, (minute + 30) % 60),
            ordinal=index,
        )
        for index, (hour, minute) in enumerate(_STARTS)
    )
    faculty = tuple(
        replace(member, availability_mask="1" * len(slots))
        for member in problem.faculty
    )
    return replace(
        problem,
        classes=classes,
        time_slots=slots,
        faculty=faculty,
        offering_block_ids=build_offering_block_ids(classes),
    )


def test_slot_draws_favor_a_compact_block_day():
    problem = _wide_grid_problem()
    sampler = GeneSampler(problem, _CONFIG)
    faculty_id = sampler.faculty_pool(OFFERING_LECTURE_ID)[0]
    # The same Block already meets 07:30-09:00 on MW and TTh.
    context = (
        ScheduleGene(
            offering_id=_OTHER_OFFERING_ID,
            class_ids=(_OTHER_CLASS_ID,),
            faculty_id=faculty_id,
            time_slot_id=problem.time_slots[0].time_slot_id,
            room_code=sampler.room_pool(_OTHER_OFFERING_ID)[0],
            meeting_days="MW",
        ),
        ScheduleGene(
            offering_id=_OTHER_OFFERING_ID,
            class_ids=(_OTHER_CLASS_ID,),
            faculty_id=faculty_id,
            time_slot_id=problem.time_slots[0].time_slot_id,
            room_code=sampler.room_pool(_OTHER_OFFERING_ID)[0],
            meeting_days="TTh",
        ),
    )
    starts = {s.time_slot_id: s.start_time for s in problem.time_slots}
    rng = Random(3)

    drawn = [
        starts[sampler.sample_gene(OFFERING_LECTURE_ID, rng, context).time_slot_id]
        for _ in range(3000)
    ]

    # Starts up to 15:00 end by 16:30: six of nine slots keep the day at 9h.
    compact_share = sum(start <= time(15) for start in drawn) / len(drawn)
    expected = COMPACT_BLOCK_DAY_PROBABILITY + (
        1 - COMPACT_BLOCK_DAY_PROBABILITY
    ) * 6 / 9
    assert abs(compact_share - expected) < 0.03
    # Soft: late slots stay reachable.
    assert time(19, 30) in drawn


def test_without_other_classes_the_rng_stream_is_unchanged():
    sampler = GeneSampler(_wide_grid_problem(), _CONFIG)
    assert [
        sampler.sample_gene(OFFERING_LECTURE_ID, Random(5)) for _ in range(3)
    ] == [
        sampler.sample_gene(OFFERING_LECTURE_ID, Random(5), ()) for _ in range(3)
    ]
