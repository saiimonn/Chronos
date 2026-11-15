import pytest

from app.cli.import_historical_faculty import (
    IdentityMapError,
    display_name,
    historical_faculty_id,
    plan_import,
    read_historical_instructors,
    read_identity_map,
)


@pytest.mark.parametrize(
    ("historical", "expected"),
    [
        ("ALCANTARA, BENEDICT S.", "Benedict S. Alcantara"),
        ("MARQUEZ, FELIX JR. L.", "Felix L. Marquez Jr."),
        ("NAVARRO, RAMON III L.", "Ramon L. Navarro III"),
        ("LIM, KAI MING ..", "Kai Ming Lim"),
        ("D'COSTA, ELIO MATTEO RAFAEL D.", "Elio Matteo Rafael D. D'Costa"),
        ("DEL MUNDO, DARIO F.", "Dario F. Del Mundo"),
        ("SARMIENTO, FR. ANDREW", "Fr. Andrew Sarmiento"),
        ("PASCUAL, RB LORENZO F.", "RB Lorenzo F. Pascual"),
        ("MUÑOZ, CARLO IÑIGO D.", "Carlo Iñigo D. Muñoz"),
        ("DIZON, MARISOL J. A.", "Marisol J. A. Dizon"),
    ],
)
def test_display_name_reorders_and_title_cases(historical: str, expected: str) -> None:
    assert display_name(historical) == expected


def _rows(*entries: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"faculty_name": name, "academic_year": year, "term_number": term}
        for name, year, term in entries
    ]


def test_instructors_are_deduplicated_with_their_period_range() -> None:
    instructors = read_historical_instructors(
        _rows(
            ("BAUTISTA, NOEL D.", "2019-2020", "2"),
            ("BAUTISTA, NOEL D.", "2018-2019", "1"),
            ("  ", "2018-2019", "1"),
            ("SALAZAR, LIZA M.", "2025-2026", "3"),
        )
    )
    assert [i.historical_name for i in instructors] == ["BAUTISTA, NOEL D.", "SALAZAR, LIZA M."]
    bautista = instructors[0]
    assert bautista.record_count == 2
    assert bautista.first_period == ("2018-2019", 1)
    assert bautista.last_period == ("2019-2020", 2)


def test_plan_skips_mapped_generated_and_same_name_roster_entries() -> None:
    instructors = read_historical_instructors(
        _rows(
            ("ALCANTARA, BENEDICT S.", "2024-2025", "1"),  # reviewed identity map
            ("BAUTISTA, NOEL D.", "2024-2025", "1"),  # already imported earlier
            ("SALAZAR, LIZA M.", "2024-2025", "1"),  # same display name on roster
            ("OCAMPO, TEODORO R.", "2019-2020", "1"),  # new
        )
    )
    reviewed_id = "4f7cee55-2dbd-483b-9b3e-705fc9334500"
    roster = {
        reviewed_id: "Benedict S. Alcantara",
        str(historical_faculty_id("BAUTISTA, NOEL D.")): "Noel D. Bautista",
        "11111111-1111-1111-1111-111111111111": "liza m. salazar",
    }
    plan = plan_import(instructors, {"ALCANTARA, BENEDICT S.": reviewed_id}, roster)

    assert [i.historical_name for i in plan.to_add] == ["OCAMPO, TEODORO R."]
    assert {fid for _, fid in plan.already_on_roster} == set(roster)


def test_spellings_of_one_name_are_added_once() -> None:
    instructors = read_historical_instructors(
        _rows(
            ("DOE, JOHN ..", "2019-2020", "1"),
            ("DOE, JOHN", "2020-2021", "1"),
        )
    )
    plan = plan_import(instructors, {}, {})

    assert [i.display_name for i in plan.to_add] == ["John Doe"]


def test_generated_ids_are_stable() -> None:
    assert historical_faculty_id("OCAMPO, TEODORO R.") == historical_faculty_id("OCAMPO, TEODORO R.")
    assert historical_faculty_id("OCAMPO, TEODORO R.") != historical_faculty_id("SALAZAR, LIZA M.")


def test_identity_map_is_required() -> None:
    with pytest.raises(IdentityMapError, match="required"):
        read_identity_map(None)


def test_missing_identity_map_is_an_error(tmp_path) -> None:
    with pytest.raises(IdentityMapError, match="not found"):
        read_identity_map(tmp_path / "missing.csv")


def test_empty_identity_map_is_an_error(tmp_path) -> None:
    path = tmp_path / "faculty_onboarding.pending.csv"
    path.write_text("historical_faculty_name,faculty_id\n", encoding="utf-8")

    with pytest.raises(IdentityMapError, match="no reviewed faculty identities"):
        read_identity_map(path)


def test_identity_map_without_reviewed_columns_is_an_error(tmp_path) -> None:
    path = tmp_path / "faculty_crosswalk.csv"
    path.write_text(
        "faculty_name,faculty_id\n"
        '"ALCANTARA, BENEDICT S.",4f7cee55-2dbd-483b-9b3e-705fc9334500\n',
        encoding="utf-8",
    )

    with pytest.raises(IdentityMapError, match="historical_faculty_name"):
        read_identity_map(path)


def test_identity_map_reads_reviewed_names(tmp_path) -> None:
    path = tmp_path / "faculty_onboarding.pending.csv"
    path.write_text(
        "historical_faculty_name,faculty_id\n"
        '"ALCANTARA, BENEDICT S.",4f7cee55-2dbd-483b-9b3e-705fc9334500\n'
        '"SARMIENTO, FR. ANDREW",7434fcb7-bffd-41b3-9c65-070c37658216\n',
        encoding="utf-8",
    )

    assert read_identity_map(path) == {
        "ALCANTARA, BENEDICT S.": "4f7cee55-2dbd-483b-9b3e-705fc9334500",
        "SARMIENTO, FR. ANDREW": "7434fcb7-bffd-41b3-9c65-070c37658216",
    }


def test_write_mapping_uses_this_databases_ids(tmp_path, monkeypatch) -> None:
    import csv

    from app.cli import import_historical_faculty as cli

    roster_id = "22222222-2222-2222-2222-222222222222"

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, _query):
            return [(roster_id, "Liza M. Salazar")]

    monkeypatch.setattr(cli, "SessionLocal", FakeSession)
    header = "faculty_name,academic_year,term_number\n"
    records = tmp_path / "records.csv"
    records.write_text(header + '"SALAZAR, LIZA M.",2024-2025,1\n')
    identity = tmp_path / "identity.csv"
    identity.write_text("historical_faculty_name,faculty_id\nX,y\n")
    mapping = tmp_path / "mapping.csv"
    args = ["--records", str(records), "--identity-map", str(identity)]

    assert cli.main([*args, "--write-mapping", str(mapping)]) == 0
    assert list(csv.DictReader(mapping.open())) == [
        {"faculty_name": "SALAZAR, LIZA M.", "faculty_id": roster_id}
    ]

    # Someone still missing from the roster: refuse rather than write a partial map.
    records.write_text(header + '"OCAMPO, TEODORO R.",2019-2020,1\n')
    assert cli.main([*args, "--write-mapping", str(mapping)]) == 2
