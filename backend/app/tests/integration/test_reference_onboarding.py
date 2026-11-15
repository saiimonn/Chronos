"""CHR-133 / CHR-COLAB-007 atomic onboarding on PostgreSQL 17."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select, text

from app.cli import onboard_rule_references as onboarding_cli
from app.domain import RoomType, SubjectType, generate_windows
from app.models import Building, Faculty, Room, Subject, Term, TimeSlot
from app.services.catalog.reference_onboarding import (
    BuildingReference,
    FacultyReference,
    ReferenceOnboardingError,
    ReviewedReferenceData,
    RoomReference,
    SubjectReference,
    TermReference,
    build_validation_receipt,
    load_database_state,
    onboard_reviewed_references,
    require_matching_receipt,
    validate_reviewed_references,
)
from app.tests.unit.test_reference_onboarding import _bundle


pytestmark = pytest.mark.postgres

FACULTY_ID = UUID("20000000-0000-0000-0000-000000000001")
BUILDING_ID = UUID("30000000-0000-0000-0000-000000000001")
TERM_ID = UUID("40000000-0000-0000-0000-000000000001")


def _data() -> ReviewedReferenceData:
    return ReviewedReferenceData(
        faculty=(FacultyReference(FACULTY_ID, "<REVIEWED FACULTY>", True),),
        subjects=(
            SubjectReference("<SUBJECT>", "<REVIEWED SUBJECT>", 3, SubjectType.LEC),
        ),
        buildings=(
            BuildingReference(BUILDING_ID, "<BUILDING>", "<BLDG>"),
        ),
        rooms=(
            RoomReference(
                "<ROOM>",
                BUILDING_ID,
                "<BUILDING>",
                45,
                RoomType.LECTURE,
                True,
            ),
        ),
        terms=(TermReference(TERM_ID, "2023-2024", 1),),
        row_counts={
            "faculty_onboarding.pending.csv": 1,
            "subjects_onboarding.pending.csv": 1,
            "rooms_onboarding.pending.csv": 1,
            "terms_onboarding.pending.csv": 1,
        },
        input_sha256={"approval_manifest.json": "1" * 64},
    )


def test_onboarding_flushes_one_atomic_catalog_and_reuses_it_on_retry(db_session):
    data = _data()

    first = onboard_reviewed_references(db_session, data)

    assert first.created == {
        "faculty": 1,
        "subjects": 1,
        "buildings": 1,
        "rooms": 1,
        "terms": 1,
        "time_slots": len(generate_windows()),
    }
    assert first.reused == {
        "faculty": 0,
        "subjects": 0,
        "buildings": 0,
        "rooms": 0,
        "terms": 0,
        "time_slots": 0,
    }
    faculty = db_session.get(Faculty, FACULTY_ID)
    assert faculty is not None
    assert faculty.availability_mask == ""
    assert faculty.availability_reviewed_term_id is None
    room = db_session.get(Room, "<ROOM>")
    assert room is not None
    assert room.building_id == BUILDING_ID
    assert room.capacity == 45
    assert db_session.scalar(
        select(func.count()).select_from(TimeSlot).where(TimeSlot.term_id == TERM_ID)
    ) == len(generate_windows())

    second = onboard_reviewed_references(db_session, data)

    assert second.created == {name: 0 for name in first.created}
    assert second.reused == {
        "faculty": 1,
        "subjects": 1,
        "buildings": 1,
        "rooms": 1,
        "terms": 1,
        "time_slots": len(generate_windows()),
    }


def test_onboarding_rejects_every_collision_before_staging_new_rows(db_session):
    db_session.add(
        Subject(
            subject_code="<SUBJECT>",
            subject_title="<DIFFERENT SUBJECT>",
            units=3,
            subject_type="LEC",
        )
    )
    db_session.flush()

    with pytest.raises(ReferenceOnboardingError, match="subject.*differs"):
        onboard_reviewed_references(db_session, _data())

    assert db_session.get(Faculty, FACULTY_ID) is None
    assert db_session.get(Building, BUILDING_ID) is None
    assert db_session.get(Room, "<ROOM>") is None
    assert db_session.get(Term, TERM_ID) is None


def test_validation_rejects_an_existing_term_with_a_noncanonical_grid(db_session):
    db_session.add(Term(
        term_id=TERM_ID,
        academic_year="2023-2024",
        term_number=1,
        is_active=False,
    ))
    db_session.add(
        TimeSlot(
            time_slot_id=UUID("50000000-0000-0000-0000-000000000001"),
            term_id=TERM_ID,
            label="<NONCANONICAL>",
            start_time=generate_windows()[0].start_time,
            end_time=generate_windows()[0].end_time,
            ordinal=0,
        )
    )
    db_session.flush()

    with pytest.raises(ReferenceOnboardingError, match="canonical time-slot grid"):
        validate_reviewed_references(db_session, _data())


def test_validation_rejects_an_active_reviewed_term(db_session):
    data = _data()
    active_term = replace(data.terms[0], is_active=True)

    with pytest.raises(ReferenceOnboardingError, match="must remain inactive"):
        validate_reviewed_references(db_session, replace(data, terms=(active_term,)))


def test_receipt_is_bound_to_database_identity_revision_and_catalog_state(db_session):
    data = _data()
    state = load_database_state(db_session)
    report = validate_reviewed_references(db_session, data)
    receipt = build_validation_receipt(data, state, report)

    require_matching_receipt(receipt, data, state)
    receipt["rules_packaged_imported_or_activated"] = True
    with pytest.raises(ReferenceOnboardingError, match="package, import or activate"):
        require_matching_receipt(receipt, data, state)
    receipt["rules_packaged_imported_or_activated"] = False

    db_session.add(
        Subject(
            subject_code="<UNRELATED>",
            subject_title="<UNRELATED SUBJECT>",
            units=3,
            subject_type="LEC",
        )
    )
    db_session.flush()
    changed = load_database_state(db_session)
    with pytest.raises(ReferenceOnboardingError, match="catalog state changed"):
        require_matching_receipt(receipt, data, changed)


def test_cli_validation_is_database_read_only_and_repeatable_read(
    migrated_engine, tmp_path, monkeypatch, capsys
):
    source, reviewed, approval = _bundle(tmp_path, partition="frozen_training")
    observed: list[tuple[str, str]] = []
    validate = onboarding_cli.validate_reviewed_references

    def observe_transaction(session, data):
        observed.append(
            (
                session.scalar(text("SHOW transaction_read_only")),
                session.scalar(text("SHOW transaction_isolation")),
            )
        )
        return validate(session, data)

    monkeypatch.setattr(onboarding_cli, "engine", migrated_engine)
    monkeypatch.setattr(
        onboarding_cli, "validate_reviewed_references", observe_transaction
    )

    assert onboarding_cli.main(
        [
            "--source-dir",
            str(source),
            "--reviewed-dir",
            str(reviewed),
            "--approval-manifest",
            str(approval),
            "--validate-only",
        ]
    ) == 0

    receipt = json.loads(capsys.readouterr().out)
    assert observed == [("on", "repeatable read")]
    assert receipt["action"] == "validated_read_only"
    assert receipt["transaction"] == {
        "isolation_level": "repeatable read",
        "read_only": True,
    }
    with migrated_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(Faculty).where(Faculty.faculty_id == FACULTY_ID)
        ) == 0


def test_cli_commit_accepts_only_the_unchanged_validation_receipt(
    migrated_engine, tmp_path, monkeypatch, capsys
):
    source, reviewed, approval = _bundle(tmp_path)
    receipt_path = tmp_path / "validation_receipt.json"
    common = [
        "--source-dir",
        str(source),
        "--reviewed-dir",
        str(reviewed),
        "--approval-manifest",
        str(approval),
    ]
    monkeypatch.setattr(onboarding_cli, "engine", migrated_engine)

    try:
        assert onboarding_cli.main([*common, "--validate-only"]) == 0
        receipt_path.write_text(capsys.readouterr().out, encoding="utf-8")

        commit_statements: list[str] = []

        def record_statement(conn, cursor, statement, parameters, context, executemany):
            commit_statements.append(statement)

        event.listen(migrated_engine, "before_cursor_execute", record_statement)
        try:
            assert onboarding_cli.main(
                [
                    *common,
                    "--commit",
                    "--confirm-reviewed",
                    "--review-manifest",
                    str(receipt_path),
                ]
            ) == 0
        finally:
            event.remove(migrated_engine, "before_cursor_execute", record_statement)

        result = json.loads(capsys.readouterr().out)
        assert result["action"] == "onboarded"
        assert result["created"]["time_slots"] == len(generate_windows())
        lock_index = next(
            index
            for index, statement in enumerate(commit_statements)
            if statement.startswith("LOCK TABLE faculty")
        )
        version_check = next(
            index
            for index, statement in enumerate(commit_statements)
            if statement == "SHOW server_version_num"
        )
        first_catalog_read = next(
            index
            for index, statement in enumerate(commit_statements)
            if "FROM faculty" in statement
        )
        assert lock_index < version_check < first_catalog_read
        with migrated_engine.connect() as connection:
            assert connection.scalar(
                select(func.count())
                .select_from(TimeSlot)
                .where(TimeSlot.term_id == TERM_ID)
            ) == len(generate_windows())
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(delete(TimeSlot).where(TimeSlot.term_id == TERM_ID))
            connection.execute(delete(Term).where(Term.term_id == TERM_ID))
            connection.execute(delete(Room).where(Room.room_code == "<ROOM>"))
            connection.execute(
                delete(Building).where(Building.building_id == BUILDING_ID)
            )
            connection.execute(
                delete(Subject).where(Subject.subject_code == "<SUBJECT>")
            )
            connection.execute(
                delete(Faculty).where(Faculty.faculty_id == FACULTY_ID)
            )
