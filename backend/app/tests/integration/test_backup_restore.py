"""CHR-179 / CHR-OPS-001: a backup restores into a clean PostgreSQL 17 database.

Seeds immutable history, a finalized schedule, an active RuleSet, an execution
job and matched-evaluation evidence into a scratch source database, backs it up
with `backend/scripts/chronos_db.sh`, restores the artifact into a second
scratch database, and compares every public table row for row plus the
constraints, indexes and triggers that protect them.

The failure paths (directory or existing output, concurrent backups, truncated
archive, a restore failing mid-load) are checked against the same source.

The scratch databases are derived from TEST_DATABASE_URL (`<name>_test` gives
`<name>_backup_{source,restore,scratch}_test`), are dropped before and after
the module, and never touch TEST_DATABASE_URL's own schema.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain import Assignment, CandidateSchedule
from app.models import JobType, UserRole
from app.models.extracted_rule import ExtractedRule
from app.models.rule_set import RuleSet, RuleSetThreshold
from app.models.term import Term
from app.repositories.jobs import JobRepository
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.services import schedules as schedule_service
from app.services.evaluation import EvaluationMode, persist_evaluation, run_matched_evaluation
from app.services.ga import GAConfig, GAEngine
from app.tests.integration.alembic_support import BACKEND_DIR, run_alembic
from app.tests.integration.test_evaluation_persistence import (
    FixedHistoricalScorer,
    fixed_historical_scores,
)
from app.tests.support.api import make_user
from app.tests.support.history import seed_historical_terms
from app.tests.support.scheduling import (
    CLASS_ID,
    FACULTY_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

SCRIPT = BACKEND_DIR / "scripts" / "chronos_db.sh"
CONTAINER = os.getenv("CHRONOS_DB_CONTAINER", "supabase_db_chronos")


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _host_client_ok() -> bool:
    """Whether chronos_db.sh uses the host client (PostgreSQL 17 or newer)."""
    if not shutil.which("pg_dump"):
        return False
    version = subprocess.run(
        ["pg_dump", "--version"], capture_output=True, text=True
    ).stdout
    return int("".join(c if c.isdigit() else " " for c in version).split()[0]) >= 17


def _script_targets(url) -> bool:
    """Whether chronos_db.sh would reach the server `url` names.

    A PostgreSQL 17 host pg_dump connects to CHRONOS_DB_HOST/PORT, which the
    tests set from `url`. Otherwise the script runs inside the Supabase
    container against that container's own server, so it only matches when
    `url` is the loopback port the container publishes.
    """
    if _host_client_ok():
        return True
    if shutil.which("docker") is None or url.host not in LOOPBACK:
        return False
    published = subprocess.run(
        ["docker", "port", CONTAINER, "5432"], capture_output=True, text=True
    )
    ports = {line.rsplit(":", 1)[-1] for line in published.stdout.split()}
    return published.returncode == 0 and str(url.port or 5432) in ports


def _run_script(
    url, *arguments: str, shims: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run chronos_db.sh; `shims` is a directory put first on PATH."""
    environment = os.environ | {
        "CHRONOS_DB_HOST": url.host or "127.0.0.1",
        "CHRONOS_DB_PORT": str(url.port or 5432),
        "PGUSER": url.username or "postgres",
        "PGPASSWORD": url.password or "",
    }
    if shims is not None:
        environment["PATH"] = f"{shims}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        [str(SCRIPT), *arguments], capture_output=True, text=True, env=environment
    )


@pytest.fixture(scope="module")
def _scratch_databases(postgres_test_url: str):
    """A migrated, seeded source database plus two empty target names."""
    base = make_url(postgres_test_url)
    if not _script_targets(base):
        pytest.skip(
            "chronos_db.sh cannot reach TEST_DATABASE_URL's server: needs a "
            "PostgreSQL 17 host pg_dump, or the Supabase db container publishing "
            "that loopback port"
        )
    stem = base.database.removesuffix("_test")
    names = [f"{stem}_backup_{role}_test" for role in ("source", "restore", "scratch")]
    admin = create_engine(base, isolation_level="AUTOCOMMIT")

    def drop(name: str) -> None:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

    for name in names:
        drop(name)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{names[0]}"'))
    source_url, restore_url, scratch_url = (base.set(database=name) for name in names)
    run_alembic(source_url.render_as_string(hide_password=False), "upgrade", "head")
    engine = create_engine(source_url)
    with Session(engine) as session:
        _seed(session)
    engine.dispose()
    yield SimpleNamespace(
        source=source_url,
        restore=restore_url,
        scratch=scratch_url,
        admin=admin,
        drop=drop,
    )
    for name in names:
        drop(name)
    admin.dispose()


@pytest.fixture
def scratch(_scratch_databases):
    """The shared source; the restore target is dropped around every test, so
    one failed assertion cannot leave a populated target for the next."""
    _scratch_databases.drop(_scratch_databases.scratch.database)
    yield _scratch_databases
    _scratch_databases.drop(_scratch_databases.scratch.database)


def _seed(session: Session) -> None:
    seed_scheduling_problem(session)
    snapshot_ids = seed_historical_terms(session)

    # Finalized schedule.
    schedule_service.persist_candidate(
        session,
        CandidateSchedule(
            schedule_id=SCHEDULE_ID,
            term_id=TERM_ID,
            assignments=(
                Assignment(
                    class_id=CLASS_ID,
                    faculty_id=FACULTY_ID,
                    time_slot_id=TIME_SLOT_ID,
                    room_code="<ROOM-1>",
                    meeting_days="MW",
                ),
            ),
        ),
    )
    schedule_service.finalize_schedule(session, SCHEDULE_ID)

    # Active RuleSet beside a superseded one, mined from the seeded history.
    source_terms = session.scalars(
        select(Term).where(Term.academic_year.in_(["2023-2024", "2024-2025"]))
    ).all()
    subject_code = make_scheduling_problem().subjects[0].subject_code
    for version, status in ((1, "superseded"), (2, "active")):
        rule_set = RuleSet(version=version, status=status, included_terms=source_terms)
        session.add(rule_set)
        session.flush()
        rule_set.thresholds.append(
            RuleSetThreshold(
                pattern_type="teacher_subject",
                min_support=Decimal("0.1000"),
                min_confidence=Decimal("0.1000"),
                min_lift=Decimal("0.0000"),
                min_term_recurrence=1,
                earliest_academic_year="2023-2024",
            )
        )
        rule_set.extracted_rules.append(
            ExtractedRule(
                pattern_type="teacher_subject",
                faculty_id=FACULTY_ID,
                subject_code=subject_code,
                support=Decimal("0.5000"),
                confidence=Decimal("0.7500"),
                lift=Decimal("1.5000"),
                term_recurrence=len(snapshot_ids),
                guidance_score=Decimal("0.6000"),
            )
        )

    # Execution job and matched-evaluation evidence.
    user = make_user(session, username="<BACKUP-ADMIN>", role=UserRole.DEPARTMENT_ADMINISTRATOR)
    JobRepository(session).enqueue(
        job_type=JobType.GA,
        requested_by=user.user_id,
        request={"term_id": str(TERM_ID)},
        timeout_seconds=60,
    )
    problem = SQLAlchemySchedulingProblemProvider(session).get_problem(TERM_ID)
    config = GAConfig(population_size=4, max_generations=1, tournament_size=2, elite_count=0)

    def runner(mode, run_config):
        scorer = FixedHistoricalScorer() if mode is EvaluationMode.GUIDED else None
        return GAEngine(problem, run_config, historical_scorer=scorer).run()

    evaluation = run_matched_evaluation(
        problem,
        sha256(b"backup fixture").hexdigest(),
        config,
        range(10),
        runner,
        lambda candidate: fixed_historical_scores(),
    )
    persist_evaluation(session, evaluation)
    session.commit()


TABLES = text(
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
)
SCHEMA_OBJECTS = {
    # Names and kinds only: PostgreSQL deparses a restored `col IN (...)` CHECK
    # with different but equivalent casts, so the text is not stable. The
    # restored CHECKs are exercised directly below instead.
    "constraints": "SELECT conrelid::regclass::text, conname, contype "
    "FROM pg_constraint WHERE connamespace = 'public'::regnamespace",
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
    "triggers": "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) "
    "FROM pg_trigger WHERE NOT tgisinternal",
}


def _fingerprint(url) -> dict[str, object]:
    """Row count and content hash per public table, plus schema objects."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            tables = connection.execute(TABLES).scalars().all()
            result: dict[str, object] = {
                name: sorted(tuple(row) for row in connection.execute(text(query)))
                for name, query in SCHEMA_OBJECTS.items()
            }
            for table in tables:
                result[table] = tuple(
                    connection.execute(
                        text(
                            f'SELECT count(*), md5(coalesce(string_agg(t::text, E\'\\n\' '
                            f'ORDER BY t::text), \'\')) FROM "{table}" t'
                        )
                    ).one()
                )
            return result
    finally:
        engine.dispose()


def test_backup_restores_into_a_clean_database(scratch, tmp_path: Path):
    source_url, restore_url = scratch.source, scratch.restore
    artifact = tmp_path / "chronos.dump"
    backup = _run_script(source_url, "backup", source_url.database, str(artifact))
    assert backup.returncode == 0, backup.stderr
    assert artifact.stat().st_size > 0

    restore = _run_script(source_url, "restore", str(artifact), restore_url.database)
    assert restore.returncode == 0, restore.stderr

    expected = _fingerprint(source_url)
    restored = _fingerprint(restore_url)
    assert restored == expected

    # The comparison is only meaningful if the evidence classes were seeded.
    for table in (
        "historical_snapshot",
        "historical_record",
        "schedule",
        "schedule_record",
        "rule_set",
        "extracted_rule",
        "execution_job",
        "evaluation_batch",
        "evaluation_run",
        "alembic_version",
    ):
        assert restored[table][0] > 0, table

    engine = create_engine(restore_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT finalized_at IS NOT NULL FROM schedule WHERE schedule_id = :id"),
                {"id": SCHEDULE_ID},
            )
            assert connection.scalar(
                text("SELECT version FROM rule_set WHERE status = 'active'")
            ) == 2
            connection.rollback()
            # Immutability triggers and CHECK / partial unique constraints came
            # back armed, not just present.
            for statement in (
                "UPDATE rule_set SET status = 'bogus' WHERE version = 1",
                "UPDATE rule_set SET status = 'active' WHERE version = 1",
                "UPDATE historical_record SET faculty_name = 'tampered'",
                "DELETE FROM historical_snapshot",
                "TRUNCATE historical_record CASCADE",
            ):
                with pytest.raises(DBAPIError):
                    with connection.begin():
                        connection.execute(text(statement))
    finally:
        engine.dispose()

    # A second restore into the now-populated database is refused untouched.
    again = _run_script(source_url, "restore", str(artifact), restore_url.database)
    assert again.returncode != 0
    assert "not empty" in again.stderr
    assert _fingerprint(restore_url) == expected


def _backup(scratch, output: Path | str) -> subprocess.CompletedProcess[str]:
    return _run_script(scratch.source, "backup", scratch.source.database, str(output))


def _partials(directory: Path) -> list[str]:
    return [path.name for path in directory.iterdir() if "partial" in path.name]


def _schema_state(url) -> tuple[int, int, int]:
    """(public schema exists, functions in public, tables in public)."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM pg_namespace WHERE nspname = 'public'),"
                        " (SELECT count(*) FROM pg_proc WHERE pronamespace ="
                        "  (SELECT oid FROM pg_namespace WHERE nspname = 'public')),"
                        " (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')"
                    )
                ).one()
            )
    finally:
        engine.dispose()


def _database_exists(scratch, url) -> bool:
    with scratch.admin.connect() as connection:
        return bool(
            connection.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
        )


def test_backup_refuses_directory_output_and_existing_archive(scratch, tmp_path: Path):
    for output in (tmp_path, f"{tmp_path}/"):
        result = _backup(scratch, output)
        assert result.returncode != 0
        assert "not a directory" in result.stderr
    assert list(tmp_path.iterdir()) == []

    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    # Archives carry password and session hashes.
    assert archive.stat().st_mode & 0o777 == 0o600
    before = archive.read_bytes()
    again = _backup(scratch, archive)
    assert again.returncode != 0
    assert "refusing to overwrite" in again.stderr
    assert archive.read_bytes() == before
    assert _partials(tmp_path) == []


def test_concurrent_backups_to_one_path_keep_one_complete_archive(
    scratch, tmp_path: Path
):
    archive = tmp_path / "race.dump"
    environment = os.environ | {
        "CHRONOS_DB_HOST": scratch.source.host,
        "CHRONOS_DB_PORT": str(scratch.source.port or 5432),
        "PGUSER": scratch.source.username,
        "PGPASSWORD": scratch.source.password,
    }
    runs = [
        subprocess.Popen(
            [str(SCRIPT), "backup", scratch.source.database, str(archive)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        for _ in range(2)
    ]
    codes = sorted(run.wait() for run in runs)
    errors = "".join(run.stderr.read() for run in runs)
    assert codes == [0, 1], errors
    assert "refusing to overwrite" in errors
    assert _partials(tmp_path) == []
    # The surviving archive restores completely.
    restore = _run_script(scratch.source, "restore", str(archive), scratch.scratch.database)
    assert restore.returncode == 0, restore.stderr


def _pg_restore() -> list[str]:
    """The `pg_restore` command chronos_db.sh uses."""
    return ["pg_restore"] if _host_client_ok() else ["docker", "exec", "-i", CONTAINER, "pg_restore"]


def _restore_accepts(content: bytes, *arguments: str) -> bool:
    """Whether the client chronos_db.sh uses runs `pg_restore` on `content`."""
    return subprocess.run(
        [*_pg_restore(), *arguments], input=content, capture_output=True
    ).returncode == 0


def _toc_end(content: bytes) -> int:
    """The smallest prefix `pg_restore -l` accepts: header and table of contents."""
    low, high = 0, len(content)
    while low < high:
        middle = (low + high) // 2
        if _restore_accepts(content[:middle], "-l"):
            high = middle
        else:
            low = middle + 1
    return low


def _table_data_blocks(content: bytes, toc_end: int) -> dict[int, tuple[int, int]]:
    """{dumpId: (start, end)} of each TABLE DATA block in a custom-format archive.

    The data blocks follow the TOC: a type byte, the entry's dumpId, then
    length-prefixed chunks ending in a zero length. Integers are a sign byte
    plus the header's int size (byte 8) little-endian bytes. Walking stops once
    every TABLE DATA entry `pg_restore -l` lists is found, so any bytes after
    the last one are never interpreted.
    """
    listing = subprocess.run(
        [*_pg_restore(), "-l"], input=content, capture_output=True, check=True
    ).stdout.decode()
    wanted = {
        int(line.split(";", 1)[0])
        for line in listing.splitlines()
        if not line.startswith(";") and " TABLE DATA " in line
    }
    int_size = content[8]

    def read_int(at: int) -> tuple[int, int]:
        value = int.from_bytes(content[at + 1 : at + 1 + int_size], "little")
        return (-value if content[at] else value), at + 1 + int_size

    blocks: dict[int, tuple[int, int]] = {}
    position = toc_end
    while wanted - blocks.keys():
        assert content[position] == 1, f"no data block at {position}"  # BLK_DATA
        dump_id, at = read_int(position + 1)
        length, at = read_int(at)
        while length:
            length, at = read_int(at + abs(length))
        if dump_id in wanted:
            blocks[dump_id] = (position, at)
        position = at
    return blocks


@pytest.mark.parametrize("where", ["inside-data", "inside-toc"])
def test_truncated_archive_is_refused_before_the_target_is_touched(
    scratch, tmp_path: Path, where: str
):
    # The archive is a header, a table of contents (what `-l` reads), then the
    # data blocks. Cut inside the largest TABLE DATA block, which every restore
    # reads, and again inside the table of contents; every client (17 and 18)
    # must refuse both. Bytes after the TOC are not all consumed (some client
    # builds never read past the last block they need), so the data cut is
    # placed in a block located by its dumpId rather than by file position.
    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    truncated = tmp_path / "truncated.dump"
    content = archive.read_bytes()
    toc_end = _toc_end(content)
    if where == "inside-data":
        start, end = max(
            _table_data_blocks(content, toc_end).values(), key=lambda b: b[1] - b[0]
        )
        cut = (start + end) // 2
        # Past the block's type, dumpId and first chunk length; before its
        # zero-length terminator.
        header = 1 + 2 * (1 + content[8])
        assert start + header < cut < end - (1 + content[8])
    else:
        cut = toc_end // 2
    # The premise: a data cut keeps the whole TOC; a TOC cut does not.
    assert _restore_accepts(content[:cut], "-l") == (where == "inside-data")
    assert not _restore_accepts(content[:cut], "-f", "/dev/null")
    truncated.write_bytes(content[:cut])

    # A missing target is not created.
    result = _run_script(scratch.source, "restore", str(truncated), scratch.scratch.database)
    assert result.returncode != 0
    assert "not a complete pg_dump archive" in result.stderr
    assert not _database_exists(scratch, scratch.scratch)

    # An existing empty target keeps its public schema.
    with scratch.admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{scratch.scratch.database}"'))
    result = _run_script(scratch.source, "restore", str(truncated), scratch.scratch.database)
    assert result.returncode != 0
    assert _schema_state(scratch.scratch) == (1, 0, 0)


def test_failed_restore_rolls_the_target_back(scratch, tmp_path: Path):
    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    with scratch.admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{scratch.scratch.database}"'))
    engine = create_engine(scratch.scratch)
    with engine.begin() as connection:
        # Outside public, so the target still counts as empty: the restore
        # drops public, recreates it, then fails at its first CREATE TABLE.
        connection.execute(text("CREATE SCHEMA guard"))
        connection.execute(
            text(
                "CREATE FUNCTION guard.block() RETURNS event_trigger LANGUAGE plpgsql "
                "AS $$BEGIN RAISE EXCEPTION 'blocked'; END$$"
            )
        )
        connection.execute(
            text(
                "CREATE EVENT TRIGGER block_tables ON ddl_command_start "
                "WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION guard.block()"
            )
        )
    engine.dispose()

    result = _run_script(scratch.source, "restore", str(archive), scratch.scratch.database)
    assert result.returncode != 0
    assert "rolled back" in result.stderr
    # public is back, empty: the target is as it was before the restore.
    assert _schema_state(scratch.scratch) == (1, 0, 0)


def test_container_mode_only_claims_its_own_published_port():
    if shutil.which("pg_dump"):
        version = subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True
        ).stdout
        if int("".join(c if c.isdigit() else " " for c in version).split()[0]) >= 17:
            pytest.skip("a PostgreSQL 17 host client honours CHRONOS_DB_HOST/PORT")
    assert not _script_targets(make_url("postgresql://u:p@192.0.2.1:54322/x_test"))
    assert not _script_targets(make_url("postgresql://u:p@127.0.0.1:1/x_test"))


# Wraps the real `docker` (container client) or `pg_restore` (host client).
# Only the load step's `pg_restore ... -f -` is altered: it emits the script up
# to one character before the end of the first course_program COPY row, so the
# cut row is still valid COPY data, then fails as a mid-stream read error does.
# check_archive's `pg_restore -f /dev/null` still sees the real archive.
TRUNCATING_SHIM = """#!{python}
import os, subprocess, sys
real = {real!r}
args = sys.argv[1:]
if "pg_restore" in (os.path.basename(real), *args) and "-" in args and \\
        args[args.index("-") - 1] == "-f":
    script = subprocess.run([real, *args], stdout=subprocess.PIPE).stdout
    copy = script.index(b"COPY public.course_program ")
    row_end = script.index(b"\\n", script.index(b"\\n", copy) + 1)
    sys.stdout.buffer.write(script[: row_end - 1])
    sys.stdout.flush()
    print("pg_restore: error: could not read from input file: end of file",
          file=sys.stderr)
    sys.exit(1)
os.execv(real, [real, *args])
"""


def _shim(directory: Path, name: str, body: str) -> None:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def test_restore_failing_inside_a_copy_reports_failure(scratch, tmp_path: Path):
    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    shims = tmp_path / "shims"
    for name in ("docker", "pg_restore"):
        real = shutil.which(name)
        if real is not None:
            _shim(shims, name, TRUNCATING_SHIM.format(python=sys.executable, real=real))

    result = _run_script(
        scratch.source, "restore", str(archive), scratch.scratch.database, shims=shims
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "restored" not in result.stdout
    assert "rolled back" in result.stderr
    assert _schema_state(scratch.scratch) == (1, 0, 0)


def test_backup_without_hard_links_still_publishes_and_refuses_overwrite(
    scratch, tmp_path: Path
):
    shims = tmp_path / "shims"
    # A filesystem without hard links: every ln fails.
    _shim(shims, "ln", "#!/bin/sh\necho 'ln: operation not supported' >&2\nexit 1\n")
    output = tmp_path / "out"
    output.mkdir()
    archive = output / "chronos.dump"

    first = _run_script(
        scratch.source, "backup", scratch.source.database, str(archive), shims=shims
    )
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == str(archive)
    before = archive.read_bytes()
    assert before

    again = _run_script(
        scratch.source, "backup", scratch.source.database, str(archive), shims=shims
    )
    assert again.returncode != 0
    assert "refusing to overwrite" in again.stderr
    assert archive.read_bytes() == before
    assert _partials(output) == []


@pytest.mark.parametrize(
    ("name", "body"),
    [
        # ln itself loses the race: the name appears, then the link fails.
        ("ln", "#!/bin/sh\nprintf theirs > \"$2\"\nexit 1\n"),
        # The name appears after ln failed for want of hard links, just
        # before the mv -n fallback publishes.
        ("mv", "#!/bin/sh\nfor last; do :; done\nprintf theirs > \"$last\"\nexec {mv} \"$@\"\n"),
    ],
    ids=["taken-at-ln", "taken-before-mv"],
)
def test_backup_refuses_a_name_taken_while_publishing(
    scratch, tmp_path: Path, name: str, body: str
):
    shims = tmp_path / "shims"
    if name == "mv":
        _shim(shims, "ln", "#!/bin/sh\nexit 1\n")
    _shim(shims, name, body.replace("{mv}", shutil.which("mv")))
    output = tmp_path / "out"
    output.mkdir()
    archive = output / "chronos.dump"

    result = _run_script(
        scratch.source, "backup", scratch.source.database, str(archive), shims=shims
    )
    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr or "could not publish" in result.stderr
    assert archive.read_bytes() == b"theirs"
    assert _partials(output) == []


# Stands in for every client the script calls. Each step named in SHIM_HANG
# (comma-separated) runs for real, emits part of its output (half of a dump; a
# load script up to the statement boundary before course_program's data;
# nothing for a check), touches SHIM_MARKER/<step> and waits. SIGINT then makes it exit 0, as `docker exec`
# does on OrbStack, so only the script's own signal handling can tell. For a
# load, the consuming psql is shielded from SIGINT so it outlives the
# producer: had a COMMIT been sent, the partial restore would be committed.
SIGNAL_SHIM = """#!{python}
import os, signal, subprocess, sys, time
real = {real!r}
args = sys.argv[1:]
tool = os.path.basename(real)
hang = os.environ.get("SHIM_HANG", "").split(",")

def step():
    if "--version" in args:
        return None
    if "pg_dump" in (tool, *args):
        return "dump"
    if "pg_restore" in (tool, *args):
        return "check" if "/dev/null" in args else "load" if "-" in args else None
    if "psql" in (tool, *args) and "ON_ERROR_STOP=1" in args:
        return "consumer"
    return None

current = step()
if current == "consumer" and "load" in hang:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sys.exit(subprocess.run([real, *args], start_new_session=True).returncode)
if current is not None and current in hang:
    output = subprocess.run([real, *args], stdout=subprocess.PIPE).stdout
    if current == "dump":
        output = output[: len(output) // 2]
    elif current == "load":
        output = output[: output.index(b"COPY public.course_program ")]
    else:
        output = b""
    signal.signal(signal.SIGINT, lambda *_: os._exit(0))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    open(os.path.join(os.environ["SHIM_MARKER"], current), "w").close()
    time.sleep(60)
    sys.exit(0)
os.execv(real, [real, *args])
"""


def _interrupt(scratch, tmp_path: Path, hang: tuple[str, ...], *arguments: str):
    """Run chronos_db.sh and Ctrl-C its process group at each step in `hang`,
    in order, stopping early if the script has already exited."""
    shims = tmp_path / "shims"
    for name in ("docker", "pg_dump", "pg_restore", "psql"):
        real = shutil.which(name)
        if real is not None:
            _shim(shims, name, SIGNAL_SHIM.format(python=sys.executable, real=real))
    markers = tmp_path / "reached"
    markers.mkdir()
    url = scratch.source
    environment = os.environ | {
        "CHRONOS_DB_HOST": url.host,
        "CHRONOS_DB_PORT": str(url.port or 5432),
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}",
        "SHIM_HANG": ",".join(hang),
        "SHIM_MARKER": str(markers),
    }
    process = subprocess.Popen(
        [str(SCRIPT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    for index, step in enumerate(hang):
        deadline = time.monotonic() + 60
        while not (markers / step).exists():
            if process.poll() is not None and index > 0:
                break  # Already stopped by the previous Ctrl-C.
            if process.poll() is not None or time.monotonic() > deadline:
                os.killpg(process.pid, signal.SIGKILL)
                pytest.fail(f"never reached {step}: {process.communicate()}")
            time.sleep(0.05)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=120)
    return process.returncode, stdout, stderr


def test_interrupted_backup_publishes_nothing(scratch, tmp_path: Path):
    output = tmp_path / "out"
    output.mkdir()
    archive = output / "chronos.dump"
    # Double Ctrl-C: during the dump, then (if still running) during the check.
    code, stdout, stderr = _interrupt(
        scratch, tmp_path, ("dump", "check"), "backup", scratch.source.database,
        str(archive),
    )
    assert code == 130, stdout + stderr
    assert "interrupted" in stderr
    assert not archive.exists()
    assert _partials(output) == []


def test_interrupted_restore_check_touches_nothing(scratch, tmp_path: Path):
    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    code, stdout, stderr = _interrupt(
        scratch, tmp_path, ("check",), "restore", str(archive), scratch.scratch.database
    )
    assert code == 130, stdout + stderr
    assert "restored" not in stdout
    assert not _database_exists(scratch, scratch.scratch)


def test_interrupted_restore_load_never_commits(scratch, tmp_path: Path):
    archive = tmp_path / "chronos.dump"
    assert _backup(scratch, archive).returncode == 0
    code, stdout, stderr = _interrupt(
        scratch, tmp_path, ("load",), "restore", str(archive), scratch.scratch.database
    )
    assert code == 130, stdout + stderr
    assert "restored" not in stdout
    # Created before the load, then rolled back to empty.
    assert _schema_state(scratch.scratch) == (1, 0, 0)


# The signal handler itself, without a database. A fake PostgreSQL 17
# `pg_dump` puts the script in host-client mode and waits to be interrupted
# (exiting 0, like `docker exec`); a `psql` shim logs the session-cleanup call
# and then returns, stalls, or runs the real psql.
FAKE_PG_DUMP = """#!/bin/sh
case " $* " in *" --version "*) echo "pg_dump (PostgreSQL) 17.6"; exit 0 ;; esac
trap 'exit 0' INT HUP TERM
touch "$SHIM_MARKER"
sleep 60 &
wait
exit 0
"""
LOGGING_PSQL = """#!/bin/sh
echo "$*" >> "$SHIM_LOG"
case "$SHIM_PSQL" in
  stall) sleep 60 ;;
  real) exec {real} "$@" ;;
esac
exit 0
"""


def _interrupt_handler(
    tmp_path: Path,
    signum: int,
    *,
    psql: str = "ok",
    host: str = "127.0.0.1",
    close_stderr: bool = False,
):
    """Interrupt a host-mode backup; return (rc, seconds to exit, psql log)."""
    shims = tmp_path / "shims"
    _shim(shims, "pg_dump", FAKE_PG_DUMP)
    _shim(shims, "psql", LOGGING_PSQL.replace("{real}", shutil.which("psql") or "false"))
    marker, log = tmp_path / "reached", tmp_path / "psql.log"
    environment = os.environ | {
        "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}",
        "CHRONOS_DB_HOST": host,
        "SHIM_MARKER": str(marker),
        "SHIM_LOG": str(log),
        "SHIM_PSQL": psql,
    }
    process = subprocess.Popen(
        [str(SCRIPT), "backup", "postgres", str(tmp_path / "out" / "x.dump")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists():
            assert process.poll() is None and time.monotonic() < deadline, "no dump"
            time.sleep(0.05)
        if close_stderr:
            process.stderr.close()  # The reader is gone: writes get EPIPE.
        started = time.monotonic()
        os.killpg(process.pid, signum)
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            code = None
        elapsed = time.monotonic() - started
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # Leftover shim sleeps.
        except ProcessLookupError:
            pass
        process.wait()
    calls = log.read_text() if log.exists() else ""
    return code, elapsed, calls


@pytest.mark.parametrize(
    ("signum", "code"), [(signal.SIGINT, 130), (signal.SIGHUP, 129), (signal.SIGTERM, 143)]
)
def test_signal_handler_survives_a_closed_stderr(tmp_path: Path, signum, code):
    rc, _, calls = _interrupt_handler(tmp_path, signum, close_stderr=True)
    assert rc == code
    assert "pg_terminate_backend" in calls


def test_signal_handler_is_bounded_when_cleanup_stalls(tmp_path: Path):
    rc, elapsed, calls = _interrupt_handler(tmp_path, signal.SIGINT, psql="stall")
    assert rc == 130
    assert "pg_terminate_backend" in calls
    assert elapsed < 10


@pytest.mark.skipif(shutil.which("psql") is None, reason="needs a host psql")
def test_signal_handler_is_bounded_when_the_server_is_unreachable(tmp_path: Path):
    # 10.255.255.1 is non-routable here: connecting hangs instead of failing.
    rc, elapsed, calls = _interrupt_handler(
        tmp_path, signal.SIGINT, psql="real", host="10.255.255.1"
    )
    assert rc == 130
    assert "pg_terminate_backend" in calls
    assert elapsed < 10
