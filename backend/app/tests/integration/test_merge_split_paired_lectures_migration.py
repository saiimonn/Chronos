"""The backfill that merges paired Blocks' lectures held on two offerings."""

from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

from app.tests.integration.alembic_support import run_alembic

pytestmark = pytest.mark.postgres

OLD_REVISION = "d4e6f8a0b2c3"
NEW_REVISION = "e5f7a9b1c3d4"

TERM = UUID(int=1)
ARCHIVED_TERM = UUID(int=2)
LECTURE = "<LEC-1>"
SCHEDULED_LECTURE = "<LEC-2>"
LAB = "<LAB-1>"


def _id(*parts: int) -> UUID:
    return UUID(int=int("".join(f"{part:03d}" for part in parts)))


def _block(term: UUID, letter: str) -> UUID:
    return _id(1 if term == TERM else 2, ord(letter))


def _offering(term: UUID, subject_index: int, group: int) -> UUID:
    return _id(3 if term == TERM else 4, subject_index, group)


def _class(term: UUID, letter: str, subject_index: int) -> UUID:
    return _id(5 if term == TERM else 6, ord(letter), subject_index)


def _seed(connection) -> None:
    def insert(sql: str, rows: list[dict]) -> None:
        connection.execute(text(sql), rows)

    insert(
        "INSERT INTO term (term_id, academic_year, term_number, is_active, is_archived) "
        "VALUES (:term_id, :year, 1, :active, :archived)",
        [
            dict(term_id=TERM, year="2026-2027", active=True, archived=False),
            dict(term_id=ARCHIVED_TERM, year="2025-2026", active=False, archived=True),
        ],
    )
    insert(
        "INSERT INTO course_program (program_code, program_name) VALUES ('<P>', '<P>')",
        [{}],
    )
    insert(
        "INSERT INTO subject (subject_code, subject_title, units, subject_type) "
        "VALUES (:code, :code, 3, :kind)",
        [
            dict(code=LECTURE, kind="LEC"),
            dict(code=SCHEDULED_LECTURE, kind="LEC"),
            dict(code=LAB, kind="LAB"),
        ],
    )
    insert(
        "INSERT INTO faculty (faculty_id, full_name, availability_mask, is_full_time) "
        "VALUES (:faculty_id, '<F>', :mask, true)",
        [dict(faculty_id=UUID(int=9), mask="1" * 48)],
    )
    subjects = {1: LECTURE, 2: SCHEDULED_LECTURE, 3: LAB}
    # Every Block on its own group: A G1, B G2, C G3, D G4.
    plan = {
        TERM: {1: "ABCD", 2: "AB", 3: "AB"},
        ARCHIVED_TERM: {1: "AB"},
    }
    for term, by_subject in plan.items():
        letters = sorted({letter for value in by_subject.values() for letter in value})
        insert(
            "INSERT INTO block_section (block_id, term_id, program_code, year_level, "
            "section_letter) VALUES (:block_id, :term_id, '<P>', 1, :letter)",
            [
                dict(block_id=_block(term, letter), term_id=term, letter=letter)
                for letter in letters
            ],
        )
        for index, taken_by in by_subject.items():
            insert(
                "INSERT INTO subject_offering (offering_id, term_id, subject_code, "
                "group_no) VALUES (:offering_id, :term_id, :code, :group_no)",
                [
                    dict(
                        offering_id=_offering(term, index, group),
                        term_id=term,
                        code=subjects[index],
                        group_no=group,
                    )
                    for group, _ in enumerate(taken_by, start=1)
                ],
            )
            insert(
                "INSERT INTO block_section_offering (class_id, block_id, offering_id, "
                "term_id, subject_code) VALUES (:class_id, :block_id, :offering_id, "
                ":term_id, :code)",
                [
                    dict(
                        class_id=_class(term, letter, index),
                        block_id=_block(term, letter),
                        offering_id=_offering(term, index, group),
                        term_id=term,
                        code=subjects[index],
                    )
                    for group, letter in enumerate(taken_by, start=1)
                ],
            )
    # 1B's second lecture is already scheduled, so it must not move.
    insert(
        "INSERT INTO schedule (schedule_id, term_id) VALUES (:schedule_id, :term_id)",
        [dict(schedule_id=UUID(int=11), term_id=TERM)],
    )
    insert(
        "INSERT INTO time_slot (time_slot_id, term_id, label, start_time, end_time, "
        "ordinal) VALUES (:time_slot_id, :term_id, '07:30-09:00', '07:30', '09:00', 1)",
        [dict(time_slot_id=UUID(int=12), term_id=TERM)],
    )
    insert(
        "INSERT INTO schedule_record (record_id, faculty_id, subject_code, term_id, "
        "schedule_id, class_id, time_slot_id, is_historical) VALUES (:record_id, "
        ":faculty_id, :code, :term_id, :schedule_id, :class_id, :time_slot_id, false)",
        [
            dict(
                record_id=UUID(int=10),
                faculty_id=UUID(int=9),
                code=SCHEDULED_LECTURE,
                term_id=TERM,
                schedule_id=UUID(int=11),
                class_id=_class(TERM, "B", 2),
                time_slot_id=UUID(int=12),
            )
        ],
    )


def _links(connection, term: UUID, subject: str) -> dict[str, tuple[UUID, int]]:
    rows = connection.execute(
        text(
            "SELECT block.section_letter, link.offering_id, offering.group_no "
            "FROM block_section_offering AS link "
            "JOIN block_section AS block ON block.block_id = link.block_id "
            "JOIN subject_offering AS offering ON offering.offering_id = link.offering_id "
            "WHERE link.term_id = :term_id AND link.subject_code = :code"
        ),
        dict(term_id=term, code=subject),
    ).all()
    return {letter: (offering_id, group_no) for letter, offering_id, group_no in rows}


def _groups(connection, term: UUID, subject: str) -> list[int]:
    return list(
        connection.execute(
            text(
                "SELECT group_no FROM subject_offering "
                "WHERE term_id = :term_id AND subject_code = :code ORDER BY group_no"
            ),
            dict(term_id=term, code=subject),
        ).scalars()
    )


_SEEDED_TABLES = (
    "schedule_record, schedule, time_slot, block_section_offering, "
    "subject_offering, block_section, faculty, subject, course_program, term"
)


def _clear(database_url: str) -> None:
    """Remove the seeded rows, which would block downgrading to base."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {_SEEDED_TABLES} CASCADE"))
    finally:
        engine.dispose()


def test_upgrade_merges_split_paired_lectures(postgres_test_url: str) -> None:
    # The shared URL fixture permits only a dedicated database ending in _test.
    run_alembic(postgres_test_url, "upgrade", "head")
    _clear(postgres_test_url)
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", OLD_REVISION)
    engine = create_engine(postgres_test_url)
    try:
        with engine.begin() as connection:
            _seed(connection)

        run_alembic(postgres_test_url, "upgrade", NEW_REVISION)

        with engine.connect() as connection:
            lecture = _links(connection, TERM, LECTURE)
            # B joins A on G1 and D joins C; the emptied groups are removed
            # and the survivors renumbered G1, G2.
            assert lecture["A"] == lecture["B"] == (_offering(TERM, 1, 1), 1)
            assert lecture["C"] == lecture["D"] == (_offering(TERM, 1, 3), 2)
            assert _groups(connection, TERM, LECTURE) == [1, 2]
            # Class ids are kept, so nothing that names a class is orphaned.
            assert connection.execute(
                text("SELECT block_id FROM block_section_offering WHERE class_id = :id"),
                dict(id=_class(TERM, "B", 1)),
            ).scalar_one() == _block(TERM, "B")

            # A scheduled class stays where it is, as do its groups.
            scheduled = _links(connection, TERM, SCHEDULED_LECTURE)
            assert scheduled["A"][0] != scheduled["B"][0]
            assert _groups(connection, TERM, SCHEDULED_LECTURE) == [1, 2]

            # Paired labs are separate classes by design.
            lab = _links(connection, TERM, LAB)
            assert lab["A"][0] != lab["B"][0]

            # Archived terms are history and stay as recorded.
            archived = _links(connection, ARCHIVED_TERM, LECTURE)
            assert archived["A"][0] != archived["B"][0]
    finally:
        engine.dispose()
        run_alembic(postgres_test_url, "upgrade", "head")
        _clear(postgres_test_url)
