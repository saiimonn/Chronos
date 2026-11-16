"""A Block's day never runs past 8 hours, first class start to last class end."""

from dataclasses import replace
from datetime import time
from uuid import UUID

from app.domain import TimeSlotDTO, build_offering_block_ids
from app.domain.meeting_patterns import meeting_end, pattern_weekdays
from app.services.ga import GAConfig, GAEngine, GeneSampler
from app.services.ga.operators import BLOCK_DAY_SPAN_MINUTES
from app.tests.support.ga import GA_TERM_ID, SECTION_LECTURE_ID, make_ga_problem

_OTHER_BLOCK_ID = UUID("00000000-0000-0000-0000-0000000000e0")
# 07:30, 09:00, ... 19:30: nine 1.5h lecture slots, a 13.5h day.
_STARTS = [(7, 30), (9, 0), (10, 30), (12, 0), (13, 30), (15, 0), (16, 30), (18, 0), (19, 30)]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _wide_grid_problem(classes_per_block: int = 4, blocks: int = 2):
    """``blocks`` Blocks of ``classes_per_block`` lectures each on a 13.5h grid."""
    problem = make_ga_problem(include_unstaffable=False)
    lecture = next(c for c in problem.classes if c.class_id == SECTION_LECTURE_ID)
    block_ids = [lecture.block_id, _OTHER_BLOCK_ID][:blocks]
    classes = tuple(
        replace(
            lecture,
            class_id=UUID(int=0xC000 + 16 * b + i),
            offering_id=UUID(int=0xD000 + 16 * b + i),
            block_id=block_id,
        )
        for b, block_id in enumerate(block_ids)
        for i in range(classes_per_block)
    )
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


def test_every_block_draws_from_one_eight_hour_window():
    problem = _wide_grid_problem()
    sampler = GeneSampler(problem, GAConfig(population_size=4, random_seed=1))
    slots = {s.time_slot_id: s for s in problem.time_slots}
    for block_id in {c.block_id for c in problem.classes}:
        meetings = [
            (_minutes(slots[slot_id].start_time), _minutes(meeting_end(slots[slot_id], days)))
            for c in problem.classes
            if c.block_id == block_id
            for faculty_id in sampler.faculty_pool(c.offering_id)
            for days in sampler.meeting_day_patterns(c.offering_id)
            for slot_id in sampler.time_slot_pool(c.offering_id, faculty_id, days)
        ]
        assert meetings
        span = max(end for _, end in meetings) - min(start for start, _ in meetings)
        assert span <= BLOCK_DAY_SPAN_MINUTES


def test_a_full_ga_run_keeps_every_block_day_within_eight_hours():
    # Without the window, seeds 1 and 2 build 9h and 10.5h days on this grid.
    for seed in (1, 2):
        _assert_block_days_within_eight_hours(seed)


def _assert_block_days_within_eight_hours(seed: int) -> None:
    problem = _wide_grid_problem()
    config = GAConfig(
        population_size=16, max_generations=15, tournament_size=2, random_seed=seed
    )
    result = GAEngine(problem, config).run()
    slots = {s.time_slot_id: s for s in problem.time_slots}
    block_of = {c.class_id: c.block_id for c in problem.classes}
    days: dict[tuple[UUID, object], list[tuple[int, int]]] = {}
    for assignment in result.candidate.assignments:
        if assignment.time_slot_id is None:
            continue
        slot = slots[assignment.time_slot_id]
        window = (
            _minutes(slot.start_time),
            _minutes(meeting_end(slot, assignment.meeting_days)),
        )
        for day in pattern_weekdays(assignment.meeting_days):
            days.setdefault((block_of[assignment.class_id], day), []).append(window)
    assert days
    for meetings in days.values():
        span = max(end for _, end in meetings) - min(start for start, _ in meetings)
        assert span <= BLOCK_DAY_SPAN_MINUTES
