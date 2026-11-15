"""The Block rules that do not need a database.

Name and capacity are derived rather than stored, so they are settled here
against a detached instance; the constraints that keep the stored columns
consistent with them are exercised in the PostgreSQL tests.
"""

from uuid import UUID

import pytest

from app.domain import BlockSectionDTO
from app.models import BlockSection


def make_block(**overrides) -> BlockSection:
    fields = {
        "program_code": "BSCS",
        "year_level": 1,
        "section_letter": "A",
        "is_expanded": False,
    }
    return BlockSection(**(fields | overrides))


def test_name_joins_program_year_and_letter() -> None:
    assert make_block().name == "BSCS-1A"


def test_name_is_uppercase_even_when_the_stored_parts_are_not() -> None:
    assert make_block(program_code="bscs", section_letter="a").name == "BSCS-1A"


def test_capacity_is_twenty_by_default() -> None:
    assert make_block().capacity == 20


def test_capacity_gains_five_when_the_block_is_expanded() -> None:
    assert make_block(is_expanded=True).capacity == 25


@pytest.mark.parametrize("year_level", [1, 5])
def test_year_level_appears_in_the_name_unpadded(year_level: int) -> None:
    assert make_block(year_level=year_level).name.endswith(f"-{year_level}A")


DTO_TERM_ID = UUID("00000000-0000-0000-0000-0000000000d1")
DTO_BLOCK_ID = UUID("00000000-0000-0000-0000-0000000000d2")


def make_block_dto(**overrides) -> BlockSectionDTO:
    fields = {
        "block_id": DTO_BLOCK_ID,
        "term_id": DTO_TERM_ID,
        "program_code": "BSCS",
        "year_level": 1,
        "section_letter": "A",
        "capacity": 20,
    }
    return BlockSectionDTO(**(fields | overrides))


def test_detached_block_name_matches_the_stored_row() -> None:
    assert make_block_dto().block_name == make_block().name


def test_detached_block_name_is_uppercase_even_when_the_parts_are_not() -> None:
    assert make_block_dto(program_code="bscs", section_letter="a").block_name == "BSCS-1A"


def test_cohort_key_identifies_the_block_within_its_term() -> None:
    assert make_block_dto().cohort_key == (DTO_TERM_ID, "BSCS", 1, "A")
