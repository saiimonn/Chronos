import uuid
from datetime import time

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.tests.integration.alembic_support import (
    BACKEND_DIR,
    run_alembic,
    run_preflight,
)


@pytest.mark.postgres
def test_postgresql_migrations_upgrade_and_match_models(postgres_test_url: str):
    # This dedicated database is reset to Alembic base before testing the full
    # upgrade path; it must never be the application's runtime database.
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", "4d8c7a1b2e6f")

    faculty_id = uuid.uuid4()
    term_id = uuid.uuid4()
    record_id = uuid.uuid4()
    current_record_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    section_id = uuid.uuid4()
    lab_section_id = uuid.uuid4()
    sibling_section_id = uuid.uuid4()
    time_slot_id = uuid.uuid4()
    engine = create_engine(postgres_test_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO faculty "
                "(faculty_id, full_name, max_workload, availability_mask) "
                # 12 units is a legacy roster value that maps to neither 18
                # nor 24 exactly; CHR-61 must still classify it part-time.
                "VALUES (:faculty_id, '<FACULTY>', 12, '1')"
            ),
            {"faculty_id": faculty_id},
        )
        connection.execute(
            text(
                "INSERT INTO subject "
                "(subject_code, subject_title, units, requires_lab) "
                "VALUES ('LEGACY101', '<SUBJECT>', 3, false), "
                "('LEGACYLAB', '<LAB-SUBJECT>', 3, true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO term "
                "(term_id, academic_year, semester, is_active) "
                "VALUES (:term_id, '2024-2025', 2, false)"
            ),
            {"term_id": term_id},
        )
        connection.execute(
            text(
                "INSERT INTO schedule_record "
                "(record_id, faculty_id, subject_code, term_id, meeting_days, "
                "time_slot, is_historical) VALUES "
                "(:record_id, :faculty_id, 'LEGACY101', :term_id, 'MWF', "
                "'08:00-09:00', true)"
            ),
            {
                "record_id": record_id,
                "faculty_id": faculty_id,
                "term_id": term_id,
            },
        )

    run_alembic(postgres_test_url, "upgrade", "a7c1e9d3b5f4")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO course_program (program_code, program_name) "
                "VALUES ('LEGACYPROG', '<PROGRAM>')"
            )
        )
        connection.execute(
            text(
                # 'LEC' with a hand-entered capacity is the pre-CHR-63 shape:
                # room_type was free text and capacity was manual. The
                # 'expanded' column does not exist at this revision.
                "INSERT INTO room (room_code, building_name, capacity, room_type) "
                "VALUES ('LEGACYROOM', '<BUILDING>', 13, 'LEC')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO time_slot "
                "(time_slot_id, term_id, label, start_time, end_time, ordinal) "
                "VALUES (:time_slot_id, :term_id, '<LEGACY-SLOT>', "
                "'08:00', '09:00', 0)"
            ),
            {"time_slot_id": time_slot_id, "term_id": term_id},
        )
        connection.execute(
            text(
                "INSERT INTO course_section "
                "(section_id, term_id, subject_code, program_code, year_level, "
                "section_letter, expected_enrollment) VALUES "
                "(:section_id, :term_id, 'LEGACY101', 'LEGACYPROG', 1, 'A', 20)"
            ),
            {"section_id": section_id, "term_id": term_id},
        )
        connection.execute(
            text(
                "INSERT INTO schedule (schedule_id, term_id, finalized_at) "
                "VALUES (:schedule_id, :term_id, now())"
            ),
            {"schedule_id": schedule_id, "term_id": term_id},
        )
        connection.execute(
            text(
                "INSERT INTO schedule_record "
                "(record_id, faculty_id, subject_code, term_id, schedule_id, "
                "section_id, time_slot_id, room_code, meeting_days, time_slot, "
                "is_historical) VALUES "
                "(:record_id, :faculty_id, 'LEGACY101', :term_id, :schedule_id, "
                ":section_id, :time_slot_id, 'LEGACYROOM', 'MWF', "
                "'08:00-09:00', false)"
            ),
            {
                "record_id": current_record_id,
                "faculty_id": faculty_id,
                "term_id": term_id,
                "schedule_id": schedule_id,
                "section_id": section_id,
                "time_slot_id": time_slot_id,
            },
        )

    # The Block cutover collapses cohorts and moves the subject onto a link, so
    # it needs more than one section to be a real test: cohort A takes two
    # subjects, and cohort B shares the lecture — two Blocks on one offering,
    # which is exactly what a LEC offering is allowed to serve.
    run_alembic(postgres_test_url, "upgrade", "c5b7e9a1d3f2")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO course_section "
                "(section_id, term_id, subject_code, program_code, year_level, "
                "section_letter, expected_enrollment) VALUES "
                "(:lab_section_id, :term_id, 'LEGACYLAB', 'LEGACYPROG', 1, 'A', 20), "
                "(:sibling_section_id, :term_id, 'LEGACY101', 'LEGACYPROG', 1, "
                "'B', 24)"
            ),
            {
                "lab_section_id": lab_section_id,
                "sibling_section_id": sibling_section_id,
                "term_id": term_id,
            },
        )

    # No subject has an offering yet, so the cutover has nothing to link to.
    # Preflight says so and refuses, rather than the migration discovering it.
    refused = run_preflight(postgres_test_url)
    assert refused.returncode == 1
    assert "missing_offering" in refused.stdout
    assert "LEGACY101" in refused.stdout and "LEGACYLAB" in refused.stdout

    applied = run_preflight(postgres_test_url, "--apply")
    assert applied.returncode == 0
    assert "created 2" in applied.stdout

    assert run_preflight(postgres_test_url).returncode == 0

    # Stop one revision short of the canonical-slot re-grid. CHR-TERM-004
    # releases every placement and replaces every hand-entered window, so the
    # evidence that the Block cutover re-pointed this record, and that its slot
    # survived the earlier migrations, only exists up to this point.
    run_alembic(postgres_test_url, "upgrade", "4002aaea2137")
    with engine.connect() as connection:
        repointed = connection.execute(
            text(
                "SELECT b.section_letter, r.class_id FROM schedule_record r "
                "JOIN block_section_offering l ON l.class_id = r.class_id "
                "JOIN block_section b ON b.block_id = l.block_id "
                "WHERE r.record_id = :record_id"
            ),
            {"record_id": current_record_id},
        ).one()
        legacy_slot = connection.execute(
            text(
                "SELECT start_time, end_time FROM time_slot "
                "WHERE time_slot_id = :time_slot_id"
            ),
            {"time_slot_id": time_slot_id},
        ).one()

    run_alembic(postgres_test_url, "upgrade", "head")

    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    with engine.connect() as connection:
        current_revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        server_version = int(connection.scalar(text("SHOW server_version_num")))
        index_definition = connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = 'uq_extracted_rule_pattern'"
            )
        )
        legacy_record = connection.execute(
            text(
                "SELECT meeting_days, time_slot, is_historical, schedule_id, "
                "class_id, time_slot_id FROM schedule_record "
                "WHERE record_id = :record_id"
            ),
            {"record_id": record_id},
        ).one()
        faculty_classification = connection.scalar(
            text(
                "SELECT is_full_time FROM faculty WHERE faculty_id = :faculty_id"
            ),
            {"faculty_id": faculty_id},
        )
        faculty_columns = set(
            connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'faculty'"
                )
            )
        )
        schedule_count = connection.scalar(text("SELECT count(*) FROM schedule"))
        finalized_at = connection.scalar(
            text(
                "SELECT finalized_at FROM schedule "
                "WHERE schedule_id = :schedule_id"
            ),
            {"schedule_id": schedule_id},
        )
        subject_types = connection.execute(
            text(
                "SELECT subject_code, subject_type FROM subject "
                "WHERE subject_code IN ('LEGACY101', 'LEGACYLAB') "
                "ORDER BY subject_code"
            )
        ).all()
        subject_columns = connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'subject'"
            )
        ).scalars().all()
        blocks = connection.execute(
            text(
                "SELECT program_code, year_level, section_letter, is_expanded "
                "FROM block_section WHERE term_id = :term_id "
                "ORDER BY section_letter"
            ),
            {"term_id": term_id},
        ).all()
        links = connection.execute(
            text(
                "SELECT b.section_letter, l.subject_code, o.group_no "
                "FROM block_section_offering l "
                "JOIN block_section b ON b.block_id = l.block_id "
                "JOIN subject_offering o ON o.offering_id = l.offering_id "
                "WHERE l.term_id = :term_id "
                "ORDER BY b.section_letter, l.subject_code"
            ),
            {"term_id": term_id},
        ).all()
        # CHR-TERM-004 released the placement: the windows it pinned no longer
        # exist, and the provenance columns go to NULL together because the
        # assignment CHECK constraint allows nothing in between.
        unplaced = connection.execute(
            text(
                "SELECT schedule_id, class_id, time_slot_id FROM schedule_record "
                "WHERE record_id = :record_id"
            ),
            {"record_id": current_record_id},
        ).one()
        canonical_grid = connection.execute(
            text(
                "SELECT label, start_time, end_time FROM time_slot "
                "WHERE term_id = :term_id ORDER BY ordinal"
            ),
            {"term_id": term_id},
        ).all()
        reset_masks = connection.scalars(
            text("SELECT DISTINCT availability_mask FROM faculty")
        ).all()
        course_section_exists = connection.scalar(
            text("SELECT to_regclass('public.course_section')")
        )
        eligibility_table_exists = connection.scalar(
            text("SELECT to_regclass('public.faculty_subject_eligibility')")
        )
        legacy_room = connection.execute(
            text(
                "SELECT capacity, room_type, expanded FROM room "
                "WHERE room_code = 'LEGACYROOM'"
            )
        ).one()

    # The free-text 'LEC' folds to the RoomType enum and the hand-entered
    # capacity of 13 is replaced by the derived, unexpanded lecture capacity.
    assert legacy_room == (40, "lecture", False)

    assert current_revision == expected_revision
    assert 170000 <= server_version < 180000
    assert index_definition is not None, "uq_extracted_rule_pattern was not created"
    assert "NULLS NOT DISTINCT" in index_definition
    assert legacy_record == (
        "MWF",
        "08:00-09:00",
        True,
        None,
        None,
        None,
    )
    # The seeded 12-unit instructor backfills to part-time, and the legacy
    # column is gone.
    assert faculty_classification is False
    assert "is_full_time" in faculty_columns
    assert "max_workload" not in faculty_columns
    assert schedule_count == 1
    # Captured one revision below head, before the re-grid replaced it.
    assert legacy_slot == (time(8), time(9))
    # After the re-grid: the placement is released and the term holds the
    # canonical Monday-Saturday 07:30-21:00 grid instead of the entered window.
    assert unplaced == (None, None, None)
    assert len(canonical_grid) == 48
    assert canonical_grid[0] == ("LEC 07:30-09:00", time(7, 30), time(9, 0))
    assert canonical_grid[47] == ("LAB 18:30-21:00", time(18, 30), time(21, 0))
    assert reset_masks == ["0" * 48]
    assert finalized_at is not None
    assert subject_types == [("LEGACY101", "LEC"), ("LEGACYLAB", "LAB")]
    assert "subject_type" in subject_columns
    assert "requires_lab" not in subject_columns

    # Two cohorts, one Block each, and cohort B keeps the expansion its former
    # enrollment of 24 needs.
    assert blocks == [
        ("LEGACYPROG", 1, "A", False),
        ("LEGACYPROG", 1, "B", True),
    ]
    assert links == [
        ("A", "LEGACY101", 1),
        ("A", "LEGACYLAB", 1),
        ("B", "LEGACY101", 1),
    ]
    # The assignment kept the identifier it already held: the class is the
    # section it replaced.
    assert repointed == ("A", section_id)
    assert course_section_exists is None
    assert eligibility_table_exists is None

    with pytest.raises(IntegrityError, match="ck_subject_type"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO subject "
                    "(subject_code, subject_title, units, subject_type) "
                    "VALUES ('INVALIDTYPE', '<SUBJECT>', 3, 'BAD')"
                )
            )

    run_alembic(postgres_test_url, "check")

    # This test writes through its own engine, so its rows are committed rather
    # than rolled back with the shared db_session transaction. Left behind they
    # are visible to every later test, and the catalogs they land in â€” faculty,
    # subject â€” are read whole rather than by term when a SchedulingProblem is
    # assembled. Removing them keeps the database as empty as the rest of the
    # suite assumes.
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schedule WHERE schedule_id = :schedule_id"),
            {"schedule_id": schedule_id},
        )
        connection.execute(
            text("DELETE FROM schedule_record WHERE term_id = :term_id"),
            {"term_id": term_id},
        )
        connection.execute(
            text("DELETE FROM block_section_offering WHERE term_id = :term_id"),
            {"term_id": term_id},
        )
        connection.execute(
            text("DELETE FROM subject_offering WHERE term_id = :term_id"),
            {"term_id": term_id},
        )
        connection.execute(
            text("DELETE FROM block_section WHERE term_id = :term_id"),
            {"term_id": term_id},
        )
        # The seeded window is gone, replaced by the canonical grid the
        # re-grid materialized for this term, so the whole grid goes here.
        connection.execute(
            text("DELETE FROM time_slot WHERE term_id = :term_id"),
            {"term_id": term_id},
        )
        connection.execute(text("DELETE FROM room WHERE room_code = 'LEGACYROOM'"))
        connection.execute(
            text("DELETE FROM course_program WHERE program_code = 'LEGACYPROG'")
        )
        connection.execute(
            text("DELETE FROM faculty WHERE faculty_id = :faculty_id"),
            {"faculty_id": faculty_id},
        )
        connection.execute(
            text(
                "DELETE FROM subject "
                "WHERE subject_code IN ('LEGACY101', 'LEGACYLAB')"
            )
        )
        connection.execute(
            text("DELETE FROM term WHERE term_id = :term_id"), {"term_id": term_id}
        )
    engine.dispose()
