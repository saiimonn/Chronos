"""Room codes are rewritten to "<BUILDING CODE><NUMBER>" with their references."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

from app.models import (
    Building,
    ExtractedRule,
    Faculty,
    Room,
    RuleSet,
    ScheduleRecord,
    Subject,
    Term,
)
from app.tests.integration.alembic_support import run_alembic

pytestmark = pytest.mark.postgres

OLD_REVISION = "d5f9b3e7a2c4"
NEW_REVISION = "e7a9c1b3d5f8"
LB = UUID(int=50)
SH = UUID(int=51)


def _room(code: str, building_id: UUID, building_name: str, **overrides):
    return {
        "room_code": code,
        "building_id": building_id,
        "building_name": building_name,
        "room_type": "lecture",
        "expanded": False,
        "capacity": 40,
        "is_active": True,
        **overrides,
    }


def test_room_codes_take_the_one_catalog_spelling(postgres_test_url: str):
    # The shared URL fixture permits only a dedicated database ending in _test.
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", OLD_REVISION)
    engine = create_engine(postgres_test_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                Building.__table__.insert(),
                [
                    dict(building_id=LB, building_name="Lawrence Bldg", building_code="LB"),
                    dict(building_id=SH, building_name="Science Hall", building_code="SH"),
                ],
            )
            lb = ("Lawrence Bldg",)
            connection.execute(
                Room.__table__.insert(),
                [
                    _room("LB486", LB, *lb),  # already right
                    _room("LB 485", LB, *lb),  # Rooms form spelling
                    _room("LB LB484", LB, *lb),  # code typed into the number field
                    _room("LB 483", LB, *lb),  # same room as "LB483": merged
                    _room("LB483", LB, *lb, is_active=False),
                    _room("LB 482", LB, *lb, room_type="laboratory", capacity=20),
                    _room("LB482", LB, *lb),  # differs from "LB 482": both kept
                    _room("SH 201", SH, "Science Hall"),
                    _room("API-101", LB, *lb),  # not "<code><number>": untouched
                ],
            )
            connection.execute(
                Faculty.__table__.insert(),
                dict(
                    faculty_id=UUID(int=1),
                    full_name="<FACULTY>",
                    is_full_time=True,
                    availability_mask="0" * 48,
                ),
            )
            connection.execute(
                Subject.__table__.insert(),
                dict(
                    subject_code="CS101",
                    subject_title="<SUBJECT>",
                    units=3,
                    subject_type="LEC",
                ),
            )
            connection.execute(
                Term.__table__.insert(),
                dict(
                    term_id=UUID(int=2),
                    academic_year="2024-2025",
                    term_number=1,
                    is_active=False,
                ),
            )
            connection.execute(
                ScheduleRecord.__table__.insert(),
                [
                    dict(
                        record_id=UUID(int=100 + n),
                        faculty_id=UUID(int=1),
                        subject_code="CS101",
                        term_id=UUID(int=2),
                        room_code=code,
                        is_historical=True,
                    )
                    for n, code in enumerate(("LB 485", "LB LB484", "LB 483", "LB 482"))
                ],
            )
            connection.execute(
                RuleSet.__table__.insert(),
                dict(
                    rule_set_id=UUID(int=10),
                    version=1,
                    status="draft",
                    generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            )
            connection.execute(
                ExtractedRule.__table__.insert(),
                [
                    dict(
                        rule_id=UUID(int=200 + n),
                        rule_set_id=UUID(int=10),
                        pattern_type="teacher_subject_room",
                        faculty_id=UUID(int=1),
                        subject_code="CS101",
                        room_code=code,
                        support="0.2500",
                        confidence="0.8000",
                        lift="1.5000",
                        term_recurrence=3,
                        guidance_score="0.6000",
                    )
                    for n, code in enumerate(("LB LB484", "LB 483"))
                ],
            )

        run_alembic(postgres_test_url, "upgrade", NEW_REVISION)

        with engine.connect() as connection:
            rooms = {
                row.room_code: row
                for row in connection.execute(
                    text("SELECT room_code, room_type, is_active FROM room")
                )
            }
            assert set(rooms) == {
                "LB486",
                "LB485",
                "LB484",
                "LB483",
                "LB 482",
                "LB482",
                "SH201",
                "API-101",
            }
            # Merged: active because one of the two spellings was.
            assert rooms["LB483"].is_active is True
            assert rooms["LB 482"].room_type == "laboratory"
            assert rooms["LB482"].room_type == "lecture"

            records = dict(
                connection.execute(
                    text("SELECT record_id, room_code FROM schedule_record")
                ).all()
            )
            assert records == {
                UUID(int=100): "LB485",
                UUID(int=101): "LB484",
                UUID(int=102): "LB483",
                UUID(int=103): "LB 482",
            }
            rules = dict(
                connection.execute(text("SELECT rule_id, room_code FROM extracted_rule")).all()
            )
            assert rules == {UUID(int=200): "LB484", UUID(int=201): "LB483"}
    finally:
        engine.dispose()
        # Restore a clean head so committed migration fixtures cannot leak.
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")
