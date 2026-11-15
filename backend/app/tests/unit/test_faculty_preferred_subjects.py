"""Faculty preferred subjects: a soft GA input that biases teacher draws."""

from dataclasses import replace
from random import Random

from app.services.ga import GAConfig, GeneSampler
from app.services.ga.operators import PREFERRED_FACULTY_PROBABILITY
from app.services.jobs.codecs import (
    decode_scheduling_problem as decode_problem,
    encode_scheduling_problem as encode_problem,
)
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    FACULTY_MORNING_ID,
    LECTURE_SUBJECT,
    OFFERING_LAB_ID,
    OFFERING_LECTURE_ID,
    make_ga_problem,
)

_CONFIG = GAConfig(
    population_size=8, max_generations=5, tournament_size=2, random_seed=1234
)


def _problem_with_morning_preferring_lecture():
    problem = make_ga_problem()
    faculty = tuple(
        replace(member, preferred_subjects=frozenset({LECTURE_SUBJECT}))
        if member.faculty_id == FACULTY_MORNING_ID
        else member
        for member in problem.faculty
    )
    return replace(problem, faculty=faculty)


def test_preferred_pool_holds_only_legal_preferring_teachers():
    sampler = GeneSampler(_problem_with_morning_preferring_lecture(), _CONFIG)

    assert sampler.preferred_faculty_pool(OFFERING_LECTURE_ID) == (
        FACULTY_MORNING_ID,
    )
    # The lab subject has no fans, so nothing is preferred there.
    assert sampler.preferred_faculty_pool(OFFERING_LAB_ID) == ()


def test_draws_favor_the_preferring_teacher_without_excluding_others():
    sampler = GeneSampler(_problem_with_morning_preferring_lecture(), _CONFIG)
    rng = Random(7)

    drawn = [
        sampler.sample_gene(OFFERING_LECTURE_ID, rng).faculty_id
        for _ in range(2000)
    ]

    share = drawn.count(FACULTY_MORNING_ID) / len(drawn)
    # 0.75 preferred + half of the remaining uniform draws.
    expected = PREFERRED_FACULTY_PROBABILITY + (1 - PREFERRED_FACULTY_PROBABILITY) / 2
    assert abs(share - expected) < 0.04
    # Soft: the non-preferring teacher is still drawn.
    assert FACULTY_FLEXIBLE_ID in drawn


def test_without_preferences_the_rng_stream_is_unchanged():
    plain = GeneSampler(make_ga_problem(), _CONFIG)
    # A preference no legal teacher holds cannot matter, so draws match.
    problem = make_ga_problem()
    unrelated = replace(
        problem,
        faculty=tuple(
            replace(member, preferred_subjects=frozenset({"<NOT-OFFERED>"}))
            for member in problem.faculty
        ),
    )
    biased = GeneSampler(unrelated, _CONFIG)

    assert plain.sample_individual(Random(99)) == biased.sample_individual(
        Random(99)
    )


def test_repair_walks_preferring_teachers_first():
    plain = GeneSampler(make_ga_problem(), _CONFIG)
    sampler = GeneSampler(_problem_with_morning_preferring_lecture(), _CONFIG)

    assert plain.repair_faculty_order(OFFERING_LECTURE_ID) == tuple(
        sorted(plain.faculty_pool(OFFERING_LECTURE_ID), key=str)
    )
    order = sampler.repair_faculty_order(OFFERING_LECTURE_ID)
    assert order[0] == FACULTY_MORNING_ID
    assert set(order) == set(sampler.faculty_pool(OFFERING_LECTURE_ID))


def test_job_payload_round_trips_preferred_subjects():
    problem = _problem_with_morning_preferring_lecture()

    encoded = encode_problem(problem)
    assert decode_problem(encoded).faculty == problem.faculty
    # A member without preferences encodes exactly as before the feature.
    plain = encode_problem(make_ga_problem())
    assert all("preferred_subjects" not in item for item in plain["faculty"])
