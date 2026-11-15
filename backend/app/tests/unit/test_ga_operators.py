from dataclasses import replace
from random import Random

import pytest

from app.domain import LockedAssignment
from app.services.ga import (
    GAConfig,
    GeneSampler,
    ScheduleGene,
    crossover,
    initialize_population,
    mutate,
)
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    FACULTY_MORNING_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    OFFERING_LECTURE_ID,
    OFFERING_LAB_ID,
    OFFERING_UNSTAFFABLE_ID,
    SECTION_LECTURE_ID,
    SECTION_LAB_ID,
    SECTION_UNSTAFFABLE_ID,
    SLOT_AFTERNOON_ID,
    SLOT_LAB_ID,
    SLOT_MORNING_ID,
    make_ga_problem,
)


def _config(**overrides) -> GAConfig:
    parameters = {
        "population_size": 8,
        "max_generations": 5,
        "tournament_size": 2,
        "random_seed": 1234,
    } | overrides
    return GAConfig(**parameters)


def _sampler(config: GAConfig | None = None) -> GeneSampler:
    return GeneSampler(make_ga_problem(), config or _config())


def _assert_structurally_valid(genes, sampler):
    assert tuple(gene.offering_id for gene in genes) == sampler.offering_ids
    for gene in genes:
        assert isinstance(gene, ScheduleGene)
        if not gene.is_resolved:
            assert gene == ScheduleGene(
                offering_id=gene.offering_id, class_ids=gene.class_ids
            )


def test_faculty_pool_is_limited_to_faculty_with_matching_availability():
    sampler = _sampler()

    assert sampler.faculty_pool(OFFERING_LECTURE_ID) == (
        FACULTY_FLEXIBLE_ID,
        FACULTY_MORNING_ID,
    )
    assert sampler.faculty_pool(OFFERING_LAB_ID) == (FACULTY_FLEXIBLE_ID,)


def _with_inactive(problem, faculty_id):
    return replace(
        problem,
        faculty=tuple(
            replace(member, is_active=False) if member.faculty_id == faculty_id else member
            for member in problem.faculty
        ),
    )


def test_inactive_faculty_are_never_drawn_into_a_pool():
    sampler = GeneSampler(_with_inactive(make_ga_problem(), FACULTY_MORNING_ID), _config())

    assert sampler.faculty_pool(OFFERING_LECTURE_ID) == (FACULTY_FLEXIBLE_ID,)


def test_an_inactive_teachers_stale_mask_does_not_stop_the_run():
    # Readiness ignores retired teachers, so a mask left over from an older
    # slot grid must not crash the GA either: that teacher is just unavailable.
    base = _with_inactive(make_ga_problem(), FACULTY_MORNING_ID)
    problem = replace(
        base,
        faculty=tuple(
            replace(member, availability_mask="1")
            if member.faculty_id == FACULTY_MORNING_ID
            else member
            for member in base.faculty
        ),
    )

    sampler = GeneSampler(problem, _config())

    assert sampler.faculty_pool(OFFERING_LECTURE_ID) == (FACULTY_FLEXIBLE_ID,)


def test_a_session_pinned_to_an_inactive_teacher_stays_placeable():
    # Another Block already holds this session with a teacher retired since;
    # the per-Block run must keep that placement, not leave it unresolved.
    base = _with_inactive(make_ga_problem(), FACULTY_MORNING_ID)
    pinned = LockedAssignment(
        class_id=SECTION_LECTURE_ID,
        faculty_id=FACULTY_MORNING_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="M",
        block_id=SECTION_LECTURE_ID,
    )
    problem = replace(base, shared_placements={OFFERING_LECTURE_ID: pinned})

    sampler = GeneSampler(problem, _config())

    assert sampler.faculty_pool(OFFERING_LECTURE_ID) == (FACULTY_MORNING_ID,)


def test_time_slot_pool_follows_availability_and_subject_duration():
    sampler = _sampler()

    assert sampler.time_slot_pool(OFFERING_LECTURE_ID, FACULTY_FLEXIBLE_ID) == (
        SLOT_MORNING_ID,
        SLOT_AFTERNOON_ID,
    )
    assert sampler.time_slot_pool(OFFERING_LECTURE_ID, FACULTY_MORNING_ID) == (
        SLOT_MORNING_ID,
    )
    assert sampler.time_slot_pool(OFFERING_LAB_ID, FACULTY_FLEXIBLE_ID) == (
        SLOT_LAB_ID,
    )


def test_time_slot_pool_is_not_materialized_for_incompatible_faculty():
    sampler = _sampler()

    with pytest.raises(KeyError):
        sampler.time_slot_pool(OFFERING_LAB_ID, FACULTY_MORNING_ID)


def test_room_pool_excludes_undersized_rooms_and_honours_room_type():
    sampler = _sampler()

    # A lecture never takes the laboratory, nor a lab the lecture room.
    assert sampler.room_pool(OFFERING_LECTURE_ID) == (LECTURE_ROOM,)
    assert sampler.room_pool(OFFERING_LAB_ID) == (LABORATORY_ROOM,)
    assert sampler.room_pool(OFFERING_UNSTAFFABLE_ID) == ()


def test_sampled_genes_only_use_values_from_their_own_pools():
    config = _config()
    sampler = _sampler(config)
    rng = Random(config.random_seed)

    for _ in range(200):
        for offering_id in sampler.offering_ids:
            gene = sampler.sample_gene(offering_id, rng)
            if not gene.is_resolved:
                continue
            assert gene.faculty_id in sampler.faculty_pool(offering_id)
            assert gene.time_slot_id in sampler.time_slot_pool(
                offering_id, gene.faculty_id
            )
            assert gene.room_code in sampler.room_pool(offering_id)
            assert gene.meeting_days in config.meeting_day_patterns


def test_offering_without_available_room_stays_unresolved():
    sampler = _sampler()
    rng = Random(7)

    gene = sampler.sample_gene(OFFERING_UNSTAFFABLE_ID, rng)

    assert gene == ScheduleGene(
        offering_id=OFFERING_UNSTAFFABLE_ID,
        class_ids=gene.class_ids,
    )


def test_initial_population_is_sized_valid_and_seed_reproducible():
    config = _config()
    sampler = _sampler(config)

    first = initialize_population(sampler, config, Random(config.random_seed))
    second = initialize_population(sampler, config, Random(config.random_seed))
    other = initialize_population(sampler, config, Random(config.random_seed + 1))

    assert len(first) == config.population_size
    for individual in first:
        _assert_structurally_valid(individual, sampler)
        assert individual.fitness.valid is False
    assert [tuple(x) for x in first] == [tuple(x) for x in second]
    assert [tuple(x) for x in first] != [tuple(x) for x in other]


def test_crossover_swaps_whole_genes_and_keeps_offering_order():
    config = _config()
    sampler = _sampler(config)
    rng = Random(config.random_seed)
    parent_a, parent_b = initialize_population(sampler, config, rng)[:2]

    child_a, child_b = crossover(parent_a, parent_b, rng)

    _assert_structurally_valid(child_a, sampler)
    _assert_structurally_valid(child_b, sampler)
    for index in range(len(sampler.offering_ids)):
        assert {child_a[index], child_b[index]} == {parent_a[index], parent_b[index]}
    assert tuple(parent_a) != tuple(child_a) or tuple(parent_b) != tuple(child_b)


def test_crossover_leaves_its_parents_untouched():
    config = _config()
    sampler = _sampler(config)
    rng = Random(config.random_seed)
    parent_a, parent_b = initialize_population(sampler, config, rng)[:2]
    before = (tuple(parent_a), tuple(parent_b))

    crossover(parent_a, parent_b, rng)

    assert (tuple(parent_a), tuple(parent_b)) == before


def test_mutation_resamples_within_pools_without_partial_genes():
    config = _config(gene_mutation_rate=1.0)
    sampler = _sampler(config)
    rng = Random(config.random_seed)
    individual = initialize_population(sampler, config, rng)[0]

    mutated = mutate(individual, sampler, config, rng)

    _assert_structurally_valid(mutated, sampler)
    for gene in mutated:
        if gene.is_resolved:
            assert gene.faculty_id in sampler.faculty_pool(gene.offering_id)
            assert gene.time_slot_id in sampler.time_slot_pool(
                gene.offering_id, gene.faculty_id
            )


def test_mutation_at_zero_rate_returns_an_identical_gene_sequence():
    config = _config(gene_mutation_rate=0.0)
    sampler = _sampler(config)
    rng = Random(config.random_seed)
    individual = initialize_population(sampler, config, rng)[0]

    assert tuple(mutate(individual, sampler, config, rng)) == tuple(individual)


def test_unresolvable_offering_remains_unresolved_after_mutation():
    config = _config(gene_mutation_rate=1.0)
    sampler = _sampler(config)
    rng = Random(config.random_seed)
    individual = initialize_population(sampler, config, rng)[0]
    index = sampler.offering_ids.index(OFFERING_UNSTAFFABLE_ID)

    for _ in range(50):
        individual = mutate(individual, sampler, config, rng)
        assert individual[index].is_resolved is False


def test_sampler_rejects_a_faculty_mask_that_does_not_match_the_slot_grid():
    problem = make_ga_problem()
    broken = replace(problem.faculty[0], availability_mask="11")

    with pytest.raises(ValueError, match="one binary digit per time slot"):
        GeneSampler(
            replace(problem, faculty=(broken,) + problem.faculty[1:]),
            _config(),
        )


def _with_inactive_room(problem, room_code):
    return replace(
        problem,
        rooms=tuple(
            replace(room, is_active=False) if room.room_code == room_code else room
            for room in problem.rooms
        ),
    )


def test_inactive_rooms_are_never_drawn_into_a_pool():
    problem = _with_inactive_room(make_ga_problem(), LECTURE_ROOM)

    sampler = GeneSampler(problem, _config())

    # The lecture room was its only legal room; the laboratory is never one.
    assert sampler.room_pool(OFFERING_LECTURE_ID) == ()
    assert sampler.room_pool(OFFERING_LAB_ID) == (LABORATORY_ROOM,)


def test_a_session_pinned_to_an_inactive_room_stays_placeable():
    base = _with_inactive_room(make_ga_problem(), LECTURE_ROOM)
    pinned = LockedAssignment(
        class_id=SECTION_LECTURE_ID,
        faculty_id=FACULTY_MORNING_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="M",
        block_id=SECTION_LECTURE_ID,
    )
    problem = replace(base, shared_placements={OFFERING_LECTURE_ID: pinned})

    sampler = GeneSampler(problem, _config())

    assert sampler.room_pool(OFFERING_LECTURE_ID) == (LECTURE_ROOM,)
