"""Master-schedule generation: no double-booking, room for a paired LAB's
partner, labs repaired first, and an even Monday-Thursday week."""

from dataclasses import replace
from uuid import UUID

import pytest

from app.domain import Assignment, CandidateSchedule, LockedAssignment
from app.domain.calendar import Weekday
from app.services.ga.chromosome import ChromosomeCodec
from app.services.ga.config import GAConfig
from app.services.ga.day_balance import (
    day_balance_score,
    lighter_patterns_first,
)
from app.services.ga.engine import GAEngine
from app.services.ga.fitness import LexicographicFitnessEvaluator
from app.services.ga.repair import DeterministicRepairOperator, RepairContext
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    GA_TERM_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    SECTION_LAB_ID,
    SECTION_LECTURE_ID,
    SLOT_AFTERNOON_ID,
    SLOT_LAB_ID,
    SLOT_MORNING_ID,
    make_congested_ga_problem,
    make_ga_problem,
)
from app.tests.unit.test_block_pairing import (
    BLOCK_B,
    CLASSES,
    FACULTY_IDS,
    LAB_A,
    LAB_B,
    LAB_OFFERING_A,
    LAB_ROOMS,
    SLOT_EVENING_LAB,
    make_paired_problem,
)


def _same_cohort(problem):
    """The fixture's lecture and lab sections, moved into one Block."""
    lecture = next(c for c in problem.classes if c.class_id == SECTION_LECTURE_ID)
    return replace(
        problem,
        classes=tuple(
            replace(
                section,
                program_code=lecture.program_code,
                year_level=lecture.year_level,
                section_letter=lecture.section_letter,
            )
            if section.class_id == SECTION_LAB_ID
            else section
            for section in problem.classes
        ),
    )


# -- Day balance ------------------------------------------------------------


def test_an_even_week_scores_higher_than_a_lopsided_one():
    problem = _same_cohort(make_ga_problem(include_unstaffable=False))
    lopsided = (
        Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "MW"),
        Assignment(SECTION_LAB_ID, FACULTY_FLEXIBLE_ID, SLOT_LAB_ID, LABORATORY_ROOM, "MW"),
    )
    even = (lopsided[0], replace(lopsided[1], meeting_days="TTh"))

    # 4h on MW and nothing on TTh, against 1h30 MW and 2h30 TTh.
    assert day_balance_score(problem, lopsided) == pytest.approx(1 / (1 + 4))
    assert day_balance_score(problem, even) == pytest.approx(1 / (1 + 1))


def test_friday_hours_do_not_count_toward_the_spread():
    problem = make_ga_problem(include_unstaffable=False)
    friday = (
        Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "F"),
    )
    assert day_balance_score(problem, friday) == 1.0


def test_lighter_days_come_first_and_friday_keeps_its_place():
    heavy_mw = {
        Weekday.MONDAY: 300.0,
        Weekday.TUESDAY: 90.0,
        Weekday.WEDNESDAY: 300.0,
        Weekday.THURSDAY: 90.0,
    }
    assert lighter_patterns_first(("MW", "TTh", "F"), heavy_mw) == ("TTh", "MW", "F")
    # A tie keeps the configured order.
    assert lighter_patterns_first(("MW", "TTh"), {}) == ("MW", "TTh")


def test_balance_weight_lifts_the_soft_score_but_not_reported_attainment():
    problem = _same_cohort(make_ga_problem(include_unstaffable=False))
    codec = ChromosomeCodec(problem)
    even = codec.encode(
        (
            Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "MW"),
            Assignment(SECTION_LAB_ID, FACULTY_FLEXIBLE_ID, SLOT_LAB_ID, LABORATORY_ROOM, "TTh"),
        )
    )
    lopsided = codec.encode(
        (
            Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID, LECTURE_ROOM, "MW"),
            Assignment(SECTION_LAB_ID, FACULTY_FLEXIBLE_ID, SLOT_LAB_ID, LABORATORY_ROOM, "MW"),
        )
    )
    plain = LexicographicFitnessEvaluator(problem)
    weighted = LexicographicFitnessEvaluator(problem, day_balance_weight=0.5)

    assert plain(even)[2] == plain(lopsided)[2] == 0.0
    assert weighted(even)[2] > weighted(lopsided)[2]
    assert weighted.historical_attainment(even) == 0.0


def test_balanced_repair_puts_a_new_class_on_the_lighter_days():
    problem = _same_cohort(make_ga_problem(include_unstaffable=False))
    lab_on_mw = Assignment(
        SECTION_LAB_ID, FACULTY_FLEXIBLE_ID, SLOT_LAB_ID, LABORATORY_ROOM, "MW"
    )
    # An incomplete lecture is infeasible on its own, so repair places it.
    lecture = Assignment(SECTION_LECTURE_ID, FACULTY_FLEXIBLE_ID, SLOT_MORNING_ID)
    candidate = CandidateSchedule(term_id=GA_TERM_ID, assignments=(lecture, lab_on_mw))
    config = GAConfig(meeting_day_patterns=("MW", "TTh"))

    balanced = DeterministicRepairOperator().repair(
        candidate, RepairContext.build(problem, config, balance_days=True)
    )
    plain = DeterministicRepairOperator().repair(
        candidate, RepairContext.build(problem, config)
    )

    by_class = {a.class_id: a for a in balanced.candidate.assignments}
    assert by_class[SECTION_LECTURE_ID].meeting_days == "TTh"
    assert {a.class_id: a for a in plain.candidate.assignments}[
        SECTION_LECTURE_ID
    ].meeting_days == "MW"


# -- Paired LABs need a second laboratory -----------------------------------


def _block_a_problem():
    """Block A alone; Block B's LAB (the partner) is not placed anywhere."""
    return replace(make_paired_problem(), classes=CLASSES[:2])


def _lab_room_taken(room: str, slot: UUID, days: str) -> LockedAssignment:
    return LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-00000000be01"),
        faculty_id=FACULTY_IDS[2],
        time_slot_id=slot,
        room_code=room,
        meeting_days=days,
        block_id=UUID("00000000-0000-0000-0000-00000000be02"),
        offering_id=UUID("00000000-0000-0000-0000-00000000be03"),
        units=1,
    )


def test_paired_lab_moves_to_a_time_with_a_lab_free_for_its_partner():
    problem = _block_a_problem()
    # Another Block holds the second lab at the LAB's time.
    lock = _lab_room_taken(LAB_ROOMS[1], SLOT_LAB_ID, "MW")
    lab = Assignment(LAB_A, FACULTY_IDS[0], SLOT_LAB_ID, LAB_ROOMS[0], "MW")
    candidate = CandidateSchedule(term_id=GA_TERM_ID, assignments=(lab,))

    result = DeterministicRepairOperator().repair(
        candidate, RepairContext.build(problem, locked_assignments=(lock,))
    )

    placed = {a.class_id: a for a in result.candidate.assignments}[LAB_A]
    assert (placed.time_slot_id, placed.meeting_days) != (SLOT_LAB_ID, "MW")
    assert result.report.is_valid


def test_paired_lab_with_a_free_second_lab_is_left_alone():
    problem = _block_a_problem()
    lab = Assignment(LAB_A, FACULTY_IDS[0], SLOT_LAB_ID, LAB_ROOMS[0], "MW")
    candidate = CandidateSchedule(term_id=GA_TERM_ID, assignments=(lab,))

    result = DeterministicRepairOperator().repair(candidate, RepairContext.build(problem))

    assert result.changed is False


def test_paired_lab_still_placed_when_no_time_leaves_a_second_lab():
    problem = _block_a_problem()
    # The second lab is busy at every lab time: placing the LAB beats leaving
    # it out, and the partner Block reports its own problem.
    locks = tuple(
        _lab_room_taken(LAB_ROOMS[1], slot, days)
        for slot in (SLOT_LAB_ID, SLOT_EVENING_LAB)
        for days in ("MW", "TTh", "F")
    )
    locks = tuple(
        replace(lock, class_id=UUID(int=index + 1), offering_id=UUID(int=index + 100))
        for index, lock in enumerate(locks)
    )
    lab = Assignment(LAB_A, FACULTY_IDS[0], SLOT_LAB_ID, LAB_ROOMS[0], "MW")
    candidate = CandidateSchedule(term_id=GA_TERM_ID, assignments=(lab,))

    result = DeterministicRepairOperator().repair(
        candidate, RepairContext.build(problem, locked_assignments=locks)
    )

    assert LAB_A in {a.class_id for a in result.candidate.assignments}


def test_partner_already_placed_needs_no_spare_room():
    problem = _block_a_problem()
    partner = _lab_room_taken(LAB_ROOMS[1], SLOT_LAB_ID, "MW")
    partner = replace(partner, class_id=LAB_B, block_id=BLOCK_B, faculty_id=FACULTY_IDS[1])
    problem = replace(problem, paired_lab_placements={LAB_OFFERING_A: partner})
    lab = Assignment(LAB_A, FACULTY_IDS[0], SLOT_LAB_ID, LAB_ROOMS[0], "MW")
    candidate = CandidateSchedule(term_id=GA_TERM_ID, assignments=(lab,))

    result = DeterministicRepairOperator().repair(
        candidate, RepairContext.build(problem, locked_assignments=(partner,))
    )

    assert result.changed is False


# -- Labs first ---------------------------------------------------------------


def test_repair_visits_lab_offerings_before_lectures():
    problem = make_paired_problem()
    codec = ChromosomeCodec(problem)
    ordered = DeterministicRepairOperator._labs_first(codec.offering_ids, problem, codec)
    lab_offerings = {codec.offering_for(LAB_A), codec.offering_for(LAB_B)}

    assert set(ordered[:2]) == lab_offerings
    assert set(ordered) == set(codec.offering_ids)


# -- No double-booked proposal ------------------------------------------------


def _too_tight(seed: int) -> GAConfig:
    # MW only: the congested term cannot hold every class without overlap.
    return GAConfig(
        random_seed=seed,
        population_size=12,
        max_generations=8,
        stagnation_limit=None,
        meeting_day_patterns=("MW",),
    )


@pytest.mark.parametrize("seed", range(6))
def test_master_generation_never_returns_a_double_booking(seed):
    result = GAEngine(
        make_congested_ga_problem(),
        _too_tight(seed),
        repair_initial_population=True,
        day_balance_weight=0.5,
    ).run()

    # What does not fit is left unplaced, never double-booked.
    assert result.metrics.hard_conflict_count == 0
    assert result.metrics.assigned_offering_count < result.metrics.required_offering_count
    assert all(metric.best_fitness[1] == 0 for metric in result.metrics.generations)


def test_without_initial_repair_a_tight_term_comes_back_double_booked():
    # The cause: fitness ranks "more placed" above "fewer conflicts", and an
    # unrepaired initial individual that places everything survives as elite.
    result = GAEngine(make_congested_ga_problem(), _too_tight(0)).run()

    assert result.metrics.hard_conflict_count > 0
