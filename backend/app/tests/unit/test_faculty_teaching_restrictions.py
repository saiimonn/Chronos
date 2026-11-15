"""Faculty teaching restrictions: non-preferred subjects and lecture-only.

Both are hard: the GA never draws a barred teacher for the subject, and the
constraint engine flags a placement that names one.
"""

from dataclasses import replace
from datetime import time

from app.domain import ConflictCode, SubjectType
from app.services.ga import GAConfig, GeneSampler
from app.services.jobs.codecs import (
    decode_scheduling_problem as decode_problem,
    encode_scheduling_problem as encode_problem,
)
from app.services.validation import ConstraintEngine
from app.tests.support.ga import (
    FACULTY_MORNING_ID,
    LECTURE_SUBJECT,
    OFFERING_LAB_ID,
    OFFERING_LECTURE_ID,
    make_ga_problem,
)
from app.tests.unit.test_constraint_engine import (
    FACULTY_1,
    SECTION_1,
    _assignment,
    _problem,
)

_CONFIG = GAConfig(
    population_size=8, max_generations=5, tournament_size=2, random_seed=1234
)


def _with_faculty(problem, faculty_id, **changes):
    return replace(
        problem,
        faculty=tuple(
            replace(member, **changes) if member.faculty_id == faculty_id else member
            for member in problem.faculty
        ),
    )


def test_sampler_never_draws_a_teacher_for_a_non_preferred_subject():
    plain = GeneSampler(make_ga_problem(), _CONFIG)
    assert FACULTY_MORNING_ID in plain.faculty_pool(OFFERING_LECTURE_ID)

    problem = _with_faculty(
        make_ga_problem(),
        FACULTY_MORNING_ID,
        non_preferred_subjects=frozenset({LECTURE_SUBJECT}),
    )
    sampler = GeneSampler(problem, _CONFIG)

    assert FACULTY_MORNING_ID not in sampler.faculty_pool(OFFERING_LECTURE_ID)
    assert FACULTY_MORNING_ID not in sampler.repair_faculty_order(OFFERING_LECTURE_ID)


def test_sampler_never_draws_a_lecture_only_teacher_for_a_lab():
    problem = make_ga_problem()
    lab_pool = GeneSampler(problem, _CONFIG).faculty_pool(OFFERING_LAB_ID)
    assert lab_pool

    restricted = replace(
        problem,
        faculty=tuple(replace(member, lecture_only=True) for member in problem.faculty),
    )
    sampler = GeneSampler(restricted, _CONFIG)

    assert sampler.faculty_pool(OFFERING_LAB_ID) == ()
    # Lectures are untouched.
    assert sampler.faculty_pool(OFFERING_LECTURE_ID) == GeneSampler(
        problem, _CONFIG
    ).faculty_pool(OFFERING_LECTURE_ID)


def test_engine_flags_a_non_preferred_subject():
    problem = _problem()
    subject_code = problem.subjects[0].subject_code
    barred = _with_faculty(
        problem, FACULTY_1, non_preferred_subjects=frozenset({subject_code})
    )

    report = ConstraintEngine().evaluate(barred, (_assignment(SECTION_1),))

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_INELIGIBLE,
    )
    assert "marked it non-preferred" in report.conflicts[0].message
    assert ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),)).is_valid


def test_engine_flags_a_lab_for_a_lecture_only_teacher():
    problem = _problem()
    lab = replace(
        problem,
        subjects=(replace(problem.subjects[0], subject_type=SubjectType.LAB),)
        + problem.subjects[1:],
        time_slots=(replace(problem.time_slots[0], end_time=time(10, 30)),)
        + problem.time_slots[1:],
    )
    lecture_only = _with_faculty(lab, FACULTY_1, lecture_only=True)

    report = ConstraintEngine().evaluate(lecture_only, (_assignment(SECTION_1),))

    codes = {conflict.code for conflict in report.conflicts}
    assert ConflictCode.FACULTY_INELIGIBLE in codes
    message = next(
        c.message for c in report.conflicts if c.code is ConflictCode.FACULTY_INELIGIBLE
    )
    assert "teaches lectures only" in message


def test_job_payload_round_trips_restrictions():
    problem = _with_faculty(
        make_ga_problem(),
        FACULTY_MORNING_ID,
        non_preferred_subjects=frozenset({LECTURE_SUBJECT}),
        lecture_only=True,
    )

    assert decode_problem(encode_problem(problem)).faculty == problem.faculty
    # An unrestricted member encodes exactly as before the feature.
    plain = encode_problem(make_ga_problem())
    assert all(
        "non_preferred_subjects" not in item and "lecture_only" not in item
        for item in plain["faculty"]
    )


def test_legacy_payload_workload_keeps_its_classification():
    encoded = encode_problem(make_ga_problem())
    for item, legacy_max in zip(encoded["faculty"], (18, 24), strict=False):
        del item["is_full_time"]
        item["max_workload"] = legacy_max
    decoded = decode_problem(encoded).faculty

    assert decoded[0].is_full_time is False
    if len(decoded) > 1:
        assert decoded[1].is_full_time is True
