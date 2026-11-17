"""A Block's day never runs past 8 hours, first class start to last class end."""

from dataclasses import replace
from datetime import time
from uuid import UUID

from app.domain import (
    Assignment,
    BlockGenerationContext,
    BlockSectionDTO,
    CandidateSchedule,
    ConflictCode,
    LockedAssignment,
    TimeSlotDTO,
    build_offering_block_ids,
)
from app.domain.meeting_patterns import meeting_end, pattern_weekdays
from app.services.ga import GAConfig, GAEngine, GeneSampler
from app.services.ga.operators import BLOCK_DAY_SPAN_MINUTES, BLOCK_RUN_LIMIT_MINUTES
from app.services.jobs import encode_block_ga_payload, run_ga
from app.services.validation import ScheduleValidator
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
        assert _longest_run(meetings) < BLOCK_RUN_LIMIT_MINUTES


def _longest_run(meetings: list[tuple[int, int]]) -> int:
    """Longest stretch of touching or overlapping meetings, in minutes."""
    longest, first, last = 0, None, None
    for start, end in sorted(meetings):
        if last is None or start > last:
            first, last = start, end
        last = max(last, end)
        longest = max(longest, last - first)
    return longest


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
        assert _longest_run(meetings) < BLOCK_RUN_LIMIT_MINUTES


def _run_conflicts(slot_indexes, *, flag: bool = True):
    problem = _wide_grid_problem()
    block_id = problem.classes[0].block_id
    classes = [c for c in problem.classes if c.block_id == block_id]
    candidate = CandidateSchedule(
        term_id=problem.term.term_id,
        assignments=tuple(
            Assignment(
                class_id=section.class_id,
                faculty_id=problem.faculty[0].faculty_id,
                time_slot_id=problem.time_slots[index].time_slot_id,
                room_code=problem.rooms[0].room_code,
                meeting_days="MW",
            )
            for section, index in zip(classes, slot_indexes, strict=False)
        ),
    )
    report = ScheduleValidator().validate(
        problem, candidate, flag_long_block_runs=flag
    ).constraint_report
    return [
        c for c in report.conflicts if c.code is ConflictCode.BLOCK_CONSECUTIVE_EXCEEDED
    ]


def test_six_hours_of_back_to_back_classes_is_a_violation():
    # 07:30-13:30 MW: four 1.5h classes in a row, exactly 6 hours, on M and W.
    conflicts = _run_conflicts([0, 1, 2, 3])
    assert len(conflicts) == 2
    assert "07:30-13:30" in conflicts[0].message


def test_a_break_or_an_unflagged_check_reports_no_run():
    # 07:30-12:00 then 13:30-15:00: 4.5h in a row at most.
    assert _run_conflicts([0, 1, 2, 4]) == []
    assert _run_conflicts([0, 1, 2, 3], flag=False) == []


def test_a_block_run_moves_its_window_past_other_blocks_full_mornings():
    # Other Blocks hold every teacher and room 07:30-16:30 on MW and TTh, the
    # case where every per-Block run took 07:30-15:30 and left classes out.
    full = _wide_grid_problem(blocks=2)
    problem = replace(
        full,
        classes=tuple(c for c in full.classes if c.block_id != _OTHER_BLOCK_ID),
        offering_block_ids=build_offering_block_ids(
            tuple(c for c in full.classes if c.block_id != _OTHER_BLOCK_ID)
        ),
    )
    locks = tuple(
        LockedAssignment(
            class_id=UUID(int=0xE000 + 64 * f + 8 * s + d),
            faculty_id=member.faculty_id,
            time_slot_id=problem.time_slots[s].time_slot_id,
            room_code=room.room_code,
            meeting_days=days,
            block_id=_OTHER_BLOCK_ID,
            offering_id=UUID(int=0xE800 + 64 * f + 8 * s + d),
            units=0,  # Only time and room matter here, not teaching load.
        )
        for f, (member, room) in enumerate(zip(problem.faculty, problem.rooms, strict=False))
        for s in range(6)
        for d, days in enumerate(("MW", "TTh"))
    )
    result = GAEngine(
        problem,
        GAConfig(population_size=16, max_generations=10, tournament_size=2, random_seed=3),
        locked_assignments=locks,
    ).run()
    assert result.metrics.assignment_completeness == 1.0
    assert result.metrics.hard_conflict_count == 0


def test_a_block_that_cannot_fit_eight_hours_is_retried_longer():
    # Two MW classes whose only teachers are free at 07:30 and at 15:00: both
    # fit only in a 9-hour day (07:30-16:30), so the 8-hour run leaves one out.
    problem = _wide_grid_problem(classes_per_block=2, blocks=1)
    early, late = problem.faculty
    faculty = (
        replace(early, availability_mask="100000000"),
        replace(late, availability_mask="000001000"),
    )
    block = problem.classes[0]
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=block.block_id,
        selected_block=BlockSectionDTO(
            block_id=block.block_id, term_id=GA_TERM_ID, program_code=block.program_code,
            year_level=block.year_level, section_letter=block.section_letter, capacity=20,
        ),
        selected_classes=problem.classes,
        locked_assignments=(),
        faculty=faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )
    config = GAConfig(population_size=8, max_generations=5, tournament_size=2,
                      random_seed=1, meeting_day_patterns=("MW",))

    result = run_ga(encode_block_ga_payload(context, config))

    assert result["metrics"]["assigned_class_count"] == 2
    assert result["block_day_span_minutes"] == 9 * 60
