"""CHR-143 / CHR-TEST-003: the scenario seed yields a term the GA can generate."""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import seed_catalog
from app.cli.seed_catalog import SeedDataError
from app.cli.seed_scenario import (
    DEFAULT_DATA_DIR,
    SCENARIO_ACADEMIC_YEAR,
    SCENARIO_TERM_NUMBER,
    ScenarioConflictError,
    scenario_readiness,
    seed_scenario,
)
from app.models import (
    BlockSectionOffering,
    Building,
    Faculty,
    HistoricalSnapshot,
    Term,
)
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.services.ga import GAConfig, GAEngine

pytestmark = pytest.mark.postgres


def _seed_empty_database(db: Session):
    seed_catalog.seed(db, seed_catalog.DEFAULT_DATA_DIR)
    db.flush()
    return seed_scenario(db)


def test_empty_database_becomes_a_ready_term_that_generates(db_session: Session):
    report = _seed_empty_database(db_session)

    assert report.term_created
    assert report.buildings.created == 2
    assert report.rooms.created == 8
    assert report.faculty.created == 12
    assert report.availability.created == 12
    assert report.blocks.created == 11
    assert report.classes.created == 51

    term = db_session.get(Term, report.term_id)
    assert term.is_active and term.academic_year == SCENARIO_ACADEMIC_YEAR
    assert set(db_session.scalars(select(Faculty.availability_reviewed_term_id))) == {
        term.term_id
    }

    readiness, warnings = scenario_readiness(db_session, term.term_id)
    assert readiness.blockers == ()
    assert readiness.ready
    assert warnings == ()

    problem = SQLAlchemySchedulingProblemProvider(db_session).get_problem(term.term_id)
    result = GAEngine(
        problem, GAConfig(population_size=10, max_generations=5, random_seed=1)
    ).run()
    assert result.metrics.assignment_completeness == 1.0
    assert result.metrics.hard_conflict_count == 0


def test_rerunning_creates_nothing_and_keeps_reviewed_masks(db_session: Session):
    first = _seed_empty_database(db_session)
    edited = db_session.scalar(select(Faculty).order_by(Faculty.full_name))
    edited.availability_mask = "1" * len(edited.availability_mask)
    db_session.flush()

    second = seed_scenario(db_session)

    assert second.term_id == first.term_id
    assert not second.term_created
    for counts in (
        second.buildings,
        second.rooms,
        second.faculty,
        second.availability,
        second.blocks,
        second.classes,
    ):
        assert counts.created == 0
    assert db_session.scalar(
        select(func.count()).select_from(BlockSectionOffering)
    ) == 51
    db_session.refresh(edited)
    assert set(edited.availability_mask) == {"1"}


@pytest.mark.parametrize(
    ("cohort", "fragment"),
    [("BSCS,5,A", "curriculum"), ("NOPE,1,A", "unknown program_code")],
)
def test_cohorts_the_catalog_cannot_serve_fail_before_any_write(
    db_session: Session, tmp_path: Path, cohort: str, fragment: str
):
    seed_catalog.seed(db_session, seed_catalog.DEFAULT_DATA_DIR)
    shutil.copytree(DEFAULT_DATA_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "cohorts.csv").write_text(
        f"program_code,year_level,section_letters\n{cohort}\n", encoding="utf-8"
    )

    with pytest.raises(SeedDataError) as raised:
        seed_scenario(db_session, tmp_path)

    assert fragment in "\n".join(raised.value.violations)
    assert db_session.scalar(select(func.count()).select_from(Term)) == 0
    assert db_session.scalar(select(func.count()).select_from(Building)) == 0


def test_a_captured_scenario_term_is_refused_before_any_write(db_session: Session):
    seed_catalog.seed(db_session, seed_catalog.DEFAULT_DATA_DIR)
    db_session.add(
        HistoricalSnapshot(
            source_term_id=uuid.uuid4(),
            academic_year=SCENARIO_ACADEMIC_YEAR,
            term_number=SCENARIO_TERM_NUMBER,
        )
    )
    db_session.flush()

    with pytest.raises(SeedDataError) as raised:
        seed_scenario(db_session)

    assert raised.value.violations == [
        f"term {SCENARIO_ACADEMIC_YEAR} T{SCENARIO_TERM_NUMBER} is a captured "
        "historical term and cannot be selected for scheduling"
    ]
    assert db_session.scalar(select(func.count()).select_from(Term)) == 0


def test_a_building_reused_by_code_skips_the_name_check(db_session: Session):
    seed_catalog.seed(db_session, seed_catalog.DEFAULT_DATA_DIR)
    db_session.add(Building(building_code="SAB", building_name="Renamed Hall"))
    db_session.add(Building(building_code="NEW", building_name="Sample Academic Building"))
    db_session.flush()

    report = seed_scenario(db_session)

    assert report.buildings.unchanged == 1
    assert report.buildings.created == 1


def test_refuses_a_different_current_term_unless_forced(db_session: Session):
    seed_catalog.seed(db_session, seed_catalog.DEFAULT_DATA_DIR)
    other = Term(academic_year="2025-2026", term_number=2, is_active=True)
    db_session.add(other)
    db_session.flush()

    with pytest.raises(ScenarioConflictError):
        seed_scenario(db_session)
    assert db_session.scalar(select(func.count()).select_from(Term)) == 1

    report = seed_scenario(db_session, force=True)

    db_session.refresh(other)
    assert not other.is_active
    assert db_session.get(Term, report.term_id).is_active
