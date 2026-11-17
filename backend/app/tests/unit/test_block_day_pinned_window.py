"""A Block's free classes stay within the day its fixed classes already set."""

from dataclasses import replace
from uuid import UUID

from app.domain import LockedAssignment, SubjectDTO, SubjectType
from app.services.ga import GAConfig, GAEngine
from app.services.job_submission import with_partner_lab_classes
from app.tests.unit.test_block_day_window import _wide_grid_problem
from app.tests.unit.test_partner_lab_reservations import _pair_context

# Slots: 07:30, 09:00, ... 19:30 (nine 1.5h lectures).
_CONFIG = GAConfig(population_size=8, max_generations=5, tournament_size=2,
                   random_seed=2, meeting_day_patterns=("MW",))


def test_fixed_classes_longer_than_the_window_do_not_drag_free_classes_further():
    # Two classes are fixed (shared with a Block already generated) at MW
    # 09:00 and 18:00: already a 10.5h day. The third may only be taught at
    # 07:30, which would stretch it to 12h, so it stays unplaced.
    problem = _wide_grid_problem(classes_per_block=3, blocks=1)
    first, second, third = problem.classes
    other = SubjectDTO(subject_code="OTHER", subject_title="x", units=3,
                       subject_type=SubjectType.LEC)
    flexible, early = problem.faculty
    slots = problem.time_slots
    room = problem.rooms[0].room_code

    def pin(section, slot):
        return LockedAssignment(
            class_id=UUID(int=0xE900 + slot), faculty_id=flexible.faculty_id,
            time_slot_id=slots[slot].time_slot_id, room_code=room, meeting_days="MW",
            block_id=UUID(int=0xE9FF), offering_id=section.offering_id, units=3,
        )

    problem = replace(
        problem,
        subjects=(*problem.subjects, other),
        classes=(first, second, replace(third, subject_code="OTHER")),
        faculty=(
            replace(flexible, availability_mask="1" * len(slots),
                    non_preferred_subjects=frozenset({"OTHER"})),
            replace(early, availability_mask="100000000"),
        ),
        shared_placements={first.offering_id: pin(first, 1), second.offering_id: pin(second, 7)},
    )
    result = GAEngine(problem, _CONFIG, repair_initial_population=True).run()
    placed = {a.class_id for a in result.candidate.assignments}
    assert third.class_id not in placed
    assert {first.class_id, second.class_id} <= placed


def test_a_partner_block_already_generated_keeps_its_own_unplaced_lab():
    # Block B ran first and left its LAB unplaced; its other classes are
    # held. Block A's run must not reserve B's LAB: B has already run.
    context, (_, lab_b) = _pair_context()
    problem = replace(context.selected_problem, classes=(*context.selected_classes, lab_b))
    held = replace(context, locked_assignments=(
        LockedAssignment(
            class_id=UUID(int=0x6F01), faculty_id=context.faculty[0].faculty_id,
            time_slot_id=context.time_slots[0].time_slot_id, room_code="LAB-1",
            meeting_days="TTh", block_id=lab_b.block_id, offering_id=UUID(int=0x6F02),
            units=3,
        ),
    ))
    assert with_partner_lab_classes(held, problem).partner_lab_classes == ()
