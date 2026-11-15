import pytest

from app.domain import academic_year_start, entry_academic_year, format_academic_year
from app.services.catalog.curriculum import select_effective_year


def test_academic_year_start_reads_the_opening_year():
    assert academic_year_start("2023-2024") == 2023


@pytest.mark.parametrize(
    "academic_year",
    ["2023", "2023/2024", "23-24", "2023-2024 ", "", "2023-2025"],
)
def test_unreadable_academic_year_is_rejected(academic_year: str):
    with pytest.raises(ValueError):
        academic_year_start(academic_year)


def test_format_academic_year_round_trips():
    assert format_academic_year(2023) == "2023-2024"
    assert academic_year_start(format_academic_year(2019)) == 2019


@pytest.mark.parametrize(
    ("year_level", "expected"),
    [
        (1, "2026-2027"),
        (2, "2025-2026"),
        (3, "2024-2025"),
        (4, "2023-2024"),
        (5, "2022-2023"),
    ],
)
def test_entry_year_walks_back_one_year_per_level(year_level: int, expected: str):
    assert entry_academic_year("2026-2027", year_level) == expected


def test_year_level_below_one_is_rejected():
    with pytest.raises(ValueError):
        entry_academic_year("2026-2027", 0)


def test_exact_match_wins():
    years = ["2019-2020", "2023-2024", "2026-2027"]
    assert select_effective_year(years, "2023-2024") == "2023-2024"


def test_falls_back_to_the_most_recent_earlier_year():
    # A prospectus stays in force until a later one replaces it, so a 2025
    # entrant with no 2025 row follows the 2023 curriculum, not the 2019 one.
    years = ["2019-2020", "2023-2024"]
    assert select_effective_year(years, "2025-2026") == "2023-2024"


def test_later_years_are_never_chosen():
    years = ["2023-2024", "2026-2027"]
    assert select_effective_year(years, "2024-2025") == "2023-2024"


def test_no_year_on_or_before_entry_resolves_to_nothing():
    # The cohort predates everything recorded; the caller must not read this as
    # "requires nothing".
    assert select_effective_year(["2023-2024"], "2019-2020") is None


def test_empty_catalog_resolves_to_nothing():
    assert select_effective_year([], "2023-2024") is None


def test_lexicographic_comparison_holds_across_a_decade_boundary():
    # Fixed-width years are why plain string comparison is safe here.
    years = ["2009-2010", "2019-2020"]
    assert select_effective_year(years, "2020-2021") == "2019-2020"
