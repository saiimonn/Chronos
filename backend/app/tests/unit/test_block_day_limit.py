"""A class no teacher can hold in its Block's window stretches only to a 10-hour day."""

from dataclasses import replace

from app.domain import SubjectDTO, SubjectType
from app.services.ga import GAConfig, GAEngine
from app.domain import ConstraintLimits

BLOCK_DAY_SPAN_LIMIT_MINUTES = ConstraintLimits().block_day_limit_hours * 60
from app.tests.unit.test_block_day_window import _wide_grid_problem

# Slots: 07:30, 09:00, ... 19:30 (nine 1.5h lectures).
_CONFIG = GAConfig(population_size=8, max_generations=5, tournament_size=2,
                   random_seed=2, meeting_day_patterns=("MW",))


def _two_class_problem(late_mask: str):
    """One class only the early teacher (07:30) may take, one only the late."""
    problem = _wide_grid_problem(classes_per_block=2, blocks=1)
    first, second = problem.classes
    other = SubjectDTO(subject_code="OTHER", subject_title="x", units=3,
                       subject_type=SubjectType.LEC)
    early, late = problem.faculty
    return replace(
        problem,
        subjects=(*problem.subjects, other),
        classes=(first, replace(second, subject_code="OTHER")),
        faculty=(
            replace(early, availability_mask="100000000",
                    non_preferred_subjects=frozenset({"OTHER"})),
            replace(late, availability_mask=late_mask,
                    non_preferred_subjects=frozenset({first.subject_code})),
        ),
    )


def _span(problem, result):
    slots = {s.time_slot_id: s for s in problem.time_slots}
    windows = [
        (slots[a.time_slot_id].start_time.hour * 60 + slots[a.time_slot_id].start_time.minute,
         slots[a.time_slot_id].end_time.hour * 60 + slots[a.time_slot_id].end_time.minute)
        for a in result.candidate.assignments
    ]
    return max(e for _, e in windows) - min(s for s, _ in windows)


def test_a_class_only_teachable_far_outside_the_window_stays_unplaced():
    # The late teacher is free only at 19:30-21:00: a 13.5h day with 07:30.
    problem = _two_class_problem("000000001")
    result = GAEngine(problem, _CONFIG, repair_initial_population=True).run()
    assert result.metrics.assigned_class_count == 1
    assert _span(problem, result) <= BLOCK_DAY_SPAN_LIMIT_MINUTES


def test_a_class_may_stretch_past_the_window_up_to_ten_hours():
    # The late teacher is free only at 15:00-16:30: a 9h day with 07:30.
    problem = _two_class_problem("000001000")
    result = GAEngine(problem, _CONFIG, repair_initial_population=True).run()
    assert result.metrics.assigned_class_count == 2
    assert _span(problem, result) <= BLOCK_DAY_SPAN_LIMIT_MINUTES
