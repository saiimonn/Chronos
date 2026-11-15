from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from app.domain import BlockClassDTO
from app.repositories import SchedulingProblemNotFoundError
from app.repositories.protocols import SchedulingProblemProvider
from app.tests.support.scheduling import (
    FixtureSchedulingProblemProvider,
    TERM_ID,
    make_candidate_schedule,
    make_scheduling_problem,
)


def load_problem(provider: SchedulingProblemProvider):
    return provider.get_problem(TERM_ID)


def test_fixture_provider_satisfies_domain_contract_deterministically():
    first = load_problem(FixtureSchedulingProblemProvider())
    second = load_problem(FixtureSchedulingProblemProvider())

    assert first == second == make_scheduling_problem()
    assert isinstance(first.classes, tuple)
    assert first.classes[0].stable_key == (
        "<PROGRAM-1>",
        1,
        "A",
        "<SUBJECT-1>",
        "00000000-0000-0000-0000-000000000003",
    )


def test_scheduling_dtos_are_immutable():
    problem = make_scheduling_problem()

    with pytest.raises(FrozenInstanceError):
        problem.term.term_number = 2


def test_candidate_schedule_fixture_is_deterministic():
    first = make_candidate_schedule()

    assert first == make_candidate_schedule()
    assert (
        first.assignments[0].class_id
        == make_scheduling_problem().classes[0].class_id
    )


def test_sections_expose_stable_cohort_identity():
    first = make_scheduling_problem().classes[0]
    second = BlockClassDTO(
        class_id=UUID("00000000-0000-0000-0000-000000000005"),
        block_id=first.block_id,
        term_id=first.term_id,
        offering_id=UUID("00000000-0000-0000-0000-000000000008"),
        group_no=1,
        subject_code="<SUBJECT-2>",
        program_code=first.program_code,
        year_level=first.year_level,
        section_letter=first.section_letter,
        capacity=first.capacity,
    )

    assert first.cohort_key == second.cohort_key
    assert first.stable_key != second.stable_key


def test_fixture_provider_rejects_unknown_term():
    unknown_term_id = UUID("00000000-0000-0000-0000-000000000099")

    with pytest.raises(SchedulingProblemNotFoundError) as exc_info:
        FixtureSchedulingProblemProvider().get_problem(unknown_term_id)

    assert exc_info.value.term_id == unknown_term_id
