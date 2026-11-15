import shutil
from pathlib import Path

import pytest

from app.cli.seed_scenario import DEFAULT_DATA_DIR, load_data
from app.cli.seed_catalog import SeedDataError


def test_committed_scenario_data_is_valid():
    data = load_data(DEFAULT_DATA_DIR)
    assert len(data.faculty) == 12


def test_every_bad_row_is_reported(tmp_path: Path):
    shutil.copytree(DEFAULT_DATA_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "rooms.csv").write_text(
        "room_code,building_code,room_type,expanded\n"
        "X 1,NOPE,office,maybe\n",
        encoding="utf-8",
    )
    (tmp_path / "faculty.csv").write_text(
        "full_name,is_full_time,available_from,available_until\n"
        "Sample,yes,18:00,07:30\n",
        encoding="utf-8",
    )

    with pytest.raises(SeedDataError) as raised:
        load_data(tmp_path)

    joined = "\n".join(raised.value.violations)
    for fragment in ("building_code", "room_type", "expanded", "is_full_time", "available_from"):
        assert fragment in joined


@pytest.mark.parametrize(
    ("filename", "content", "fragment"),
    [
        (
            "buildings.csv",
            "building_code,building_name\nSAB,Hall\nSTL,HALL\n",
            "duplicate building_name",
        ),
        (
            "buildings.csv",
            f"building_code,building_name\n{'X' * 21},Hall\n",
            "buildings.csv:2: building_code: String should have at most 20",
        ),
        (
            "faculty.csv",
            "full_name,is_full_time,available_from,available_until\n"
            "A,true,07:30+08:00,18:00+08:00\n",
            "HH:MM",
        ),
        (
            "faculty.csv",
            "full_name,is_full_time,available_from,available_until\n"
            "A,true,07:31,07:59\n",
            "holds no time slot",
        ),
        (
            "cohorts.csv",
            "program_code,year_level,section_letters\nBSCS,1,A-1\n",
            "section_letters",
        ),
    ],
)
def test_values_the_services_would_refuse_fail_up_front(
    tmp_path: Path, filename: str, content: str, fragment: str
):
    shutil.copytree(DEFAULT_DATA_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / filename).write_text(content, encoding="utf-8")

    with pytest.raises(SeedDataError) as raised:
        load_data(tmp_path)

    assert fragment in "\n".join(raised.value.violations)
