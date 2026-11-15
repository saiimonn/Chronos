"""CHR-203 / CHR-FIX-030: a delete racing a referencing insert answers 409, not 500.

Catalog DELETE is now a soft retire; a building retire still takes the same
row lock so a racing room insert is counted rather than stranded.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import APIError
from app.models import Building, BlockSectionOffering, Room
from app.services.catalog import buildings, guards, offerings, subjects
from app.tests.integration.test_schedule import make_problem_data

pytestmark = pytest.mark.postgres


def _building(code: str) -> Building:
    return Building(building_name=f"Race {code}", building_code=code)


def _room(code: str, building: Building) -> Room:
    return Room(
        room_code=code,
        building_id=building.building_id,
        building_name=building.building_name,
        capacity=10,
        room_type="lecture",
    )


def test_delete_waits_for_concurrent_referencing_insert_then_409(migrated_engine):
    make_session = sessionmaker(bind=migrated_engine)
    with make_session() as setup:
        building = _building("RACE1")
        setup.add(building)
        setup.commit()
        building_id = building.building_id
    inserter, deleter = make_session(), make_session()
    try:
        # The insert takes FOR KEY SHARE on the building and stays uncommitted.
        inserter.add(_room("RACE1-1", building))
        inserter.flush()

        def run_delete():
            try:
                buildings.deactivate_building(deleter, building_id)
            except APIError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_delete)
            # The delete must queue on the building lock rather than count 0.
            for _ in range(100):
                if future.done():
                    break
                with migrated_engine.connect() as probe:
                    waiting = probe.scalar(
                        text(
                            "select query from pg_stat_activity "
                            "where wait_event_type = 'Lock' "
                            "and datname = current_database() limit 1"
                        )
                    )
                if waiting:
                    break
                time.sleep(0.05)
            assert not future.done()
            # It is the retire's own row lock that waits, not a later write.
            assert waiting is not None
            assert waiting.lstrip().upper().startswith("SELECT")
            assert "FOR UPDATE" in waiting.upper()
            inserter.commit()
            error = future.result(timeout=10)
        assert error is not None
        assert error.status_code == 409
        assert error.code == "DEPENDENT_RECORDS_EXIST"
        assert error.details["dependents"] == {"rooms": 1}
    finally:
        inserter.close()
        deleter.close()
        with make_session() as cleanup:
            cleanup.execute(delete(Room).where(Room.room_code == "RACE1-1"))
            cleanup.execute(
                delete(Building).where(Building.building_id == building_id)
            )
            cleanup.commit()


def _blind_first_count(monkeypatch) -> None:
    """Make the first dependent count miss, as a racing insert would."""
    real = guards.ensure_not_referenced
    calls = []

    def blind_first_time(db, checks):
        calls.append(1)
        if len(calls) > 1:
            real(db, checks)

    monkeypatch.setattr(guards, "ensure_not_referenced", blind_first_time)


def test_foreign_key_violation_is_a_409_even_if_the_count_missed_it(
    db_session: Session, monkeypatch
):
    make_problem_data(db_session)
    offering_id = db_session.scalar(select(BlockSectionOffering.offering_id))
    # Commit releases the setup savepoint so the backstop's rollback keeps it.
    db_session.commit()
    _blind_first_count(monkeypatch)

    with pytest.raises(APIError) as caught:
        offerings.delete_offering(db_session, offering_id)

    assert caught.value.status_code == 409
    assert caught.value.code == "DEPENDENT_RECORDS_EXIST"
    assert caught.value.details["dependents"] == {"block_classes": 1}


def test_retiring_a_subject_leaves_its_offerings_untouched(db_session: Session):
    # A hard delete here used to rely on the FK backstop; a retire never
    # issues a DELETE, so the offering survives and keeps its subject code.
    make_problem_data(db_session)
    db_session.commit()

    subjects.deactivate_subject(db_session, "CS300")

    assert db_session.scalar(
        select(BlockSectionOffering.subject_code).where(
            BlockSectionOffering.subject_code == "CS300"
        )
    ) == "CS300"
