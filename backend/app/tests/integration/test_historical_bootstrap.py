"""CHR-108 / CHR-ING-001 PostgreSQL bootstrap atomicity and read-only checks."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.cli import import_history as cli
from app.models import (
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    RuleSet,
    ScheduleRecord,
    Subject,
    Term,
)
from app.services.arm.preprocessing import MiningMode, prepare_training_data
from app.services.arm.service import load_historical_terms
from app.services.ingestion.historical_file import (
    HistoricalImportError,
    load_historical_import,
)
from app.services.ingestion.historical_import import (
    import_historical_data,
    validate_historical_import,
)
from app.tests.integration.alembic_support import run_alembic
from app.tests.support.history import add_to_curriculum
from app.tests.support.historical_bootstrap import (
    FACULTY_ID,
    FACULTY_NAME,
    rewrite_as_teacher_subject_tier,
    write_bootstrap_dataset,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def cli_engine(migrated_engine):
    """A separately owned database lets CLI commit tests leave durable history."""
    name = f"chronos_cli_{uuid4().hex}_test"
    with migrated_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = migrated_engine.url.set(database=name)
    engine = create_engine(url)
    try:
        run_alembic(url.render_as_string(hide_password=False), "upgrade", "head")
        yield engine
    finally:
        engine.dispose()
        with migrated_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _inputs(session, tmp_path):
    faculty = Faculty(
        faculty_id=FACULTY_ID,
        full_name=FACULTY_NAME,
        is_full_time=False,
        availability_mask="",
    )
    session.add(faculty)
    session.add_all(
        Term(academic_year="2023-2024", term_number=n, is_active=False) for n in (1, 2)
    )
    session.flush()
    dataset, crosswalk = write_bootstrap_dataset(tmp_path)
    return load_historical_import(dataset, crosswalk)


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_validation_is_select_only_and_reports_retired_catalog_values(
    db_session, tmp_path
):
    data = _inputs(db_session, tmp_path)
    connection = db_session.connection()
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(connection, "before_cursor_execute", capture)
    try:
        validation = validate_historical_import(db_session, data)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert statements and set(statements) == {"SELECT"}
    assert len(validation.term_ids) == 2
    assert validation.unknown_subject_count > 0
    assert validation.unknown_room_count > 0
    assert (
        _count(db_session, HistoricalSnapshot)
        == _count(db_session, HistoricalRecord)
        == 0
    )


def test_validation_does_not_autoflush_unrelated_pending_rows(db_session, tmp_path):
    data = _inputs(db_session, tmp_path)
    pending = Faculty(full_name="UNFLUSHED", is_full_time=False, availability_mask="")
    db_session.add(pending)
    validate_historical_import(db_session, data)
    assert pending.faculty_id is None
    assert pending in db_session.new
    with db_session.no_autoflush:
        assert _count(db_session, Faculty) == 1


def test_import_creates_only_nonempty_snapshots_and_preserves_history(
    db_session, tmp_path
):
    data = _inputs(db_session, tmp_path)
    snapshot_ids = import_historical_data(db_session, data)
    assert len(snapshot_ids) == 2
    assert (
        _count(db_session, HistoricalSnapshot)
        == _count(db_session, HistoricalRecord)
        == 2
    )
    assert _count(db_session, ScheduleRecord) == _count(db_session, RuleSet) == 0
    for source in data.records:
        stored = db_session.get(HistoricalRecord, source.historical_record_id)
        assert stored.source_record_id is None
        assert stored.source_faculty_id == source.source_faculty_id
        assert (stored.faculty_name, stored.subject_code, stored.subject_title) == (
            source.faculty_name,
            source.subject_code,
            source.subject_title,
        )
        assert (stored.room_code, stored.meeting_days, stored.time_slot) == (
            source.room_code,
            source.meeting_days,
            source.time_slot,
        )
    db_session.add(
        Subject(
            subject_code="SUBJ-1",
            subject_title="Synthetic Subject A",
            units=3,
            subject_type="LEC",
        )
    )
    db_session.flush()
    add_to_curriculum(db_session, "SUBJ-1")
    terms = load_historical_terms(db_session)
    assert len(terms) == 2
    assert sum(len(term.offerings) for term in terms) == len(data.records)
    faculty = db_session.get(Faculty, FACULTY_ID)
    faculty.full_name = "RENAMED CURRENT FACULTY"
    db_session.flush()
    db_session.expire_all()
    assert all(
        db_session.get(HistoricalRecord, row.historical_record_id).faculty_name
        == row.faculty_name
        for row in data.records
    )


@pytest.mark.parametrize("continuation", [False, True])
def test_teacher_subject_tier_history_is_stored_without_a_placement(
    db_session, tmp_path, continuation,
):
    db_session.add(
        Faculty(
            faculty_id=FACULTY_ID,
            full_name=FACULTY_NAME,
            is_full_time=False,
            availability_mask="",
        )
    )
    db_session.add_all(
        Term(academic_year="2019-2020", term_number=n, is_active=False) for n in (1, 2)
    )
    db_session.flush()
    dataset, crosswalk = write_bootstrap_dataset(tmp_path, continuation=continuation)
    rewrite_as_teacher_subject_tier(dataset, keep_placement=continuation)

    import_historical_data(db_session, load_historical_import(dataset, crosswalk))

    stored = db_session.scalars(select(HistoricalRecord)).all()
    assert len(stored) == 2 + int(continuation)
    assert {
        (record.subject_code, record.meeting_days, record.time_slot, record.room_code)
        for record in stored
    } == {("SUBJ-1", None, None, None)}
    db_session.add(
        Subject(
            subject_code="SUBJ-1",
            subject_title="Synthetic Subject A",
            units=3,
            subject_type="LEC",
        )
    )
    db_session.flush()
    add_to_curriculum(db_session, "SUBJ-1")
    prepared = prepare_training_data(
        load_historical_terms(db_session), mode=MiningMode.OPERATIONAL
    )
    assert [len(stream.transactions) for stream in prepared] == [2 + int(continuation), 0, 0, 0]


def test_repeat_import_is_refused_without_changing_history(db_session, tmp_path):
    data = _inputs(db_session, tmp_path)
    import_historical_data(db_session, data)
    with pytest.raises(HistoricalImportError, match="already captured"):
        import_historical_data(db_session, data)
    assert (
        _count(db_session, HistoricalSnapshot)
        == _count(db_session, HistoricalRecord)
        == 2
    )


@pytest.mark.parametrize("missing", ["faculty", "term", "active_term"])
def test_reference_or_active_term_failure_precedes_writes(
    db_session, tmp_path, missing
):
    data = _inputs(db_session, tmp_path)
    if missing == "faculty":
        data = replace(
            data,
            records=tuple(
                replace(row, source_faculty_id=uuid4()) for row in data.records
            ),
        )
    elif missing == "term":
        term = db_session.scalars(select(Term).where(Term.term_number == 2)).one()
        db_session.delete(term)
        db_session.flush()
    else:
        term = db_session.scalars(select(Term).where(Term.term_number == 2)).one()
        term.is_active = True
        db_session.flush()
    with pytest.raises(HistoricalImportError):
        import_historical_data(db_session, data)
    assert (
        _count(db_session, HistoricalSnapshot)
        == _count(db_session, HistoricalRecord)
        == 0
    )


def test_database_failure_rolls_back_the_whole_import_savepoint(db_session, tmp_path):
    data = _inputs(db_session, tmp_path)
    invalid = replace(data.records[-1], subject_title=" ")
    data = replace(data, records=(*data.records[:-1], invalid))
    with pytest.raises(HistoricalImportError, match="no partial import"):
        import_historical_data(db_session, data)
    assert (
        _count(db_session, HistoricalSnapshot)
        == _count(db_session, HistoricalRecord)
        == 0
    )
    assert _count(db_session, Faculty) == 1
    assert _count(db_session, Term) == 2


def test_import_does_not_flush_or_commit_pending_caller_changes(db_session, tmp_path):
    data = _inputs(db_session, tmp_path)
    pending = Faculty(full_name="PENDING", is_full_time=False, availability_mask="")
    db_session.add(pending)
    with pytest.raises(HistoricalImportError, match="pending changes"):
        import_historical_data(db_session, data)
    assert pending in db_session.new and pending.faculty_id is None
    with db_session.no_autoflush:
        assert _count(db_session, HistoricalSnapshot) == 0


def test_capture_race_is_atomic_and_preserves_other_transaction_state(
    db_session, tmp_path, monkeypatch
):
    data = _inputs(db_session, tmp_path)
    from app.services.ingestion import historical_import

    original = historical_import.validate_historical_import

    def concurrent_capture(session, import_data):
        result = original(session, import_data)
        period, term_id = result.term_ids[0]
        session.add(
            HistoricalSnapshot(
                source_term_id=term_id, academic_year=period[0], term_number=period[1]
            )
        )
        session.flush()
        return result

    monkeypatch.setattr(
        historical_import, "validate_historical_import", concurrent_capture
    )
    with pytest.raises(HistoricalImportError, match="no partial import"):
        import_historical_data(db_session, data)
    assert _count(db_session, HistoricalSnapshot) == 1
    assert _count(db_session, HistoricalRecord) == 0


def test_import_holds_term_locks_until_outer_transaction_ends(
    cli_engine, tmp_path, monkeypatch
):
    from app.services.ingestion import historical_import

    with Session(cli_engine) as session:
        data = _inputs(session, tmp_path)
        session.commit()
    original = historical_import.validate_historical_import
    attempted = []

    def validate_while_other_session_activates(session, import_data):
        with cli_engine.connect() as rival:
            rival.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                rival.execute(
                    text(
                        "UPDATE term SET is_active = true WHERE academic_year = '2023-2024' AND term_number = 1"
                    )
                )
            rival.rollback()
            attempted.append(True)
        return original(session, import_data)

    monkeypatch.setattr(
        historical_import,
        "validate_historical_import",
        validate_while_other_session_activates,
    )
    with Session(cli_engine) as session:
        import_historical_data(session, data)
        assert attempted == [True]
        session.rollback()


@pytest.mark.parametrize("fail_commit", [False, True])
def test_cli_confirmed_commit_is_durable_or_fully_rolled_back(
    cli_engine, tmp_path, monkeypatch, capsys, fail_commit
):
    import json

    with Session(cli_engine) as session:
        _inputs(session, tmp_path)
        session.commit()
    monkeypatch.setattr(cli, "engine", cli_engine)
    dataset, crosswalk = tmp_path / "dataset", tmp_path / "faculty_crosswalk.csv"
    args = ["--dataset-dir", str(dataset), "--faculty-crosswalk", str(crosswalk)]
    assert cli.main([*args, "--validate-only"]) == 0
    receipt = tmp_path / "review.json"
    receipt.write_text(capsys.readouterr().out, encoding="utf-8")
    if fail_commit:

        class FailingCommitSession(Session):
            def commit(self):
                raise OperationalError(
                    "private dsn password", {}, RuntimeError("secret")
                )

        monkeypatch.setattr(cli, "Session", FailingCommitSession)
    result = cli.main(
        [*args, "--commit", "--confirm-reviewed", "--review-manifest", str(receipt)]
    )
    output = capsys.readouterr()
    assert result == (1 if fail_commit else 0)
    with Session(cli_engine) as session:
        expected = 0 if fail_commit else 2
        assert (
            _count(session, HistoricalSnapshot)
            == _count(session, HistoricalRecord)
            == expected
        )
    if fail_commit:
        assert not output.out
        assert "secret" not in output.err and "private dsn password" not in output.err
    else:
        assert json.loads(output.out)["action"] == "imported"


def test_cli_validation_uses_database_enforced_read_only_transaction(
    migrated_engine, tmp_path, monkeypatch, capsys
):
    faculty_id = uuid4()
    dataset, crosswalk = write_bootstrap_dataset(tmp_path, faculty_id=faculty_id)
    term_ids = [uuid4(), uuid4()]
    with Session(migrated_engine) as session:
        session.add(
            Faculty(
                faculty_id=faculty_id,
                full_name=FACULTY_NAME,
                is_full_time=False,
                availability_mask="",
            )
        )
        session.add_all(
            Term(
                term_id=term_id,
                academic_year="2023-2024",
                term_number=n,
                is_active=False,
            )
            for n, term_id in enumerate(term_ids, 1)
        )
        session.commit()
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement.lstrip().split()[0].upper())

    monkeypatch.setattr(cli, "engine", migrated_engine)
    event.listen(migrated_engine, "before_cursor_execute", capture)
    try:
        assert (
            cli.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--faculty-crosswalk",
                    str(crosswalk),
                    "--validate-only",
                ]
            )
            == 0
        )
        assert statements and set(statements) <= {"SELECT", "SHOW"}
        assert "validated_read_only" in capsys.readouterr().out
        with Session(migrated_engine) as session:
            assert (
                _count(session, HistoricalSnapshot)
                == _count(session, HistoricalRecord)
                == 0
            )
    finally:
        event.remove(migrated_engine, "before_cursor_execute", capture)
        with Session(migrated_engine) as session:
            session.execute(delete(Term).where(Term.term_id.in_(term_ids)))
            session.execute(delete(Faculty).where(Faculty.faculty_id == faculty_id))
            session.commit()
