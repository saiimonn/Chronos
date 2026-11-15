"""CHR-126 / CHR-ARM-006 fenced, idempotent ARM publication in PostgreSQL.

Retains the CHR-31 / CHR-WORKER-001 version-collision savepoint regression.
All mining inputs are synthetic; no original workbook or holdout is read.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import AppUser, Faculty, JobStatus, JobType, Room, Subject, UserRole
from app.models.extracted_rule import ExtractedRule
from app.models.historical_snapshot import HistoricalSnapshot
from app.models.rule_set import RuleSet, RuleSetThreshold, rule_set_term
from app.models.term import Term
from app.repositories.jobs import JobRepository
from app.services import rule_sets as rule_set_service
from app.services.arm import (
    MiningMode,
    HistoricalOffering,
    HistoricalTerm,
    MiningThresholds,
    PatternStream,
)
from app.services.arm.mining import RuleMiner
from app.services.arm.preprocessing import prepare_training_data
from app.services.arm.providers import DatabaseRuleProvider
from app.services.jobs.codecs import (
    CODEC_VERSION,
    encode_historical_terms,
    encode_mining_thresholds,
)
from app.services.jobs.handlers import _persist_draft_ruleset, run_arm


pytestmark = pytest.mark.postgres


def _historical_terms() -> tuple[HistoricalTerm, ...]:
    return tuple(
        HistoricalTerm(
            snapshot_id=UUID(f"10000000-0000-0000-0000-{index:012d}"),
            academic_year=f"{2023 + index}-{2024 + index}",
            term_number=1,
            offerings=(
                HistoricalOffering(
                    historical_record_id=UUID(
                        f"20000000-0000-0000-0000-{index:012d}"
                    ),
                    faculty_id=UUID("30000000-0000-0000-0000-000000000001"),
                    subject_code="<SUBJECT-1>",
                    meeting_days="MWF",
                    time_slot="08:00-09:00",
                    room_code="<ROOM-1>",
                ),
            ),
        )
        for index in range(1, 8)
    )


def _candidate(terms):
    thresholds = {
        stream: MiningThresholds(
            min_support=Decimal("0.1"),
            min_confidence=Decimal("0.1"),
            min_lift=Decimal("0"),
            min_term_recurrence=1,
        )
        for stream in PatternStream
    }
    candidate = RuleMiner().mine(
        prepare_training_data(terms, mode=MiningMode.OPERATIONAL), thresholds
    )
    assert len(candidate.rules) == len(PatternStream)
    return candidate, terms


@pytest.fixture
def clean_rule_sets(migrated_engine):
    """Drop every rule set the test created, including the committed rival."""
    with Session(migrated_engine) as session, session.begin():
        preexisting = set(session.scalars(select(RuleSet.rule_set_id)).all())
    yield preexisting
    with Session(migrated_engine) as session, session.begin():
        surviving = set(session.scalars(select(RuleSet.rule_set_id)).all())
        added = surviving - preexisting
        if added:
            session.execute(
                text("DELETE FROM rule_set WHERE rule_set_id = ANY(:ids)"),
                {"ids": list(added)},
            )


@pytest.fixture
def arm_publication(migrated_engine, clean_rule_sets):
    user_id, faculty_id = uuid4(), uuid4()
    subject_code = f"<ARM-{faculty_id.hex[:8]}>"
    room_code = f"<ROOM-{faculty_id.hex[:8]}>"
    terms = tuple(
        replace(
            term,
            snapshot_id=uuid4(),
            offerings=tuple(
                replace(
                    item,
                    faculty_id=faculty_id,
                    subject_code=subject_code,
                    room_code=room_code,
                )
                for item in term.offerings
            ),
        )
        for term in _historical_terms()
    )
    candidate, terms = _candidate(terms)
    with Session(migrated_engine) as session, session.begin():
        session.add_all(
            [
                AppUser(
                    user_id=user_id,
                    username=f"arm-publication-{user_id}",
                    password_hash="$argon2id$test-only-placeholder",
                    role=UserRole.DEPARTMENT_ADMINISTRATOR,
                ),
                Faculty(
                    faculty_id=faculty_id,
                    full_name="<TBD>",
                    is_full_time=True,
                    availability_mask="0",
                ),
                Subject(
                    subject_code=subject_code,
                    subject_title="<TBD>",
                    units=3,
                    subject_type="LEC",
                ),
                Room(
                    room_code=room_code,
                    building_name="<TBD>",
                    capacity=30,
                    room_type="lecture",
                    expanded=False,
                ),
            ]
        )
        session.flush()
        queued = JobRepository(session).enqueue(
            job_type=JobType.ARM,
            requested_by=user_id,
            request={"version": CODEC_VERSION},
            timeout_seconds=120,
            max_attempts=2,
        )
        claimed = JobRepository(session).claim_next(
            claimed_by="arm-publication-test", lease_seconds=300,
            job_types=[JobType.ARM],
        )
        assert claimed is not None and claimed.job_id == queued.job_id
        assert claimed.claim_token is not None
    try:
        yield claimed, candidate, terms
    finally:
        with Session(migrated_engine) as session, session.begin():
            # Also clean up unlinked sets if publication fails its job-link test.
            session.execute(
                delete(RuleSet).where(RuleSet.rule_set_id.not_in(clean_rule_sets))
            )
            session.execute(
                text("DELETE FROM execution_job WHERE requested_by = :user_id"),
                {"user_id": user_id},
            )
            session.execute(delete(AppUser).where(AppUser.user_id == user_id))
            session.execute(delete(Faculty).where(Faculty.faculty_id == faculty_id))
            session.execute(delete(Subject).where(Subject.subject_code == subject_code))
            session.execute(delete(Room).where(Room.room_code == room_code))


def _stored_publication(session, rule_set_id):
    """Include row identities and every stored value, not just child counts."""
    rule_set_id = UUID(rule_set_id)
    return tuple(
        tuple(
            session.execute(
                select(table)
                .where(table.c.rule_set_id == rule_set_id)
                .order_by(*table.primary_key.columns)
            ).all()
        )
        for table in (
            RuleSet.__table__, RuleSetThreshold.__table__,
            ExtractedRule.__table__, rule_set_term,
        )
    )


def _publication_counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(table))
        for table in (
            RuleSet, RuleSetThreshold, ExtractedRule, rule_set_term,
        )
    )


def test_version_collision_retries_without_aborting_the_caller_transaction(
    migrated_engine, arm_publication
):
    """A rival that commits the claimed version must cost one attempt, not the write.

    Rolling back the whole session here (rather than to a savepoint) raises
    ``InvalidRequestError: Can't operate on closed transaction inside context
    manager`` on the next statement, so the retry never runs and no rule set
    lands at all.
    """
    claimed, candidate, terms = arm_publication
    rival_versions = []

    with Session(migrated_engine) as session, session.begin():
        original_scalar = session.scalar

        def claim_version_first(statement, *args, **kwargs):
            # Runs as the handler reads max(version): a separate connection
            # commits the version the handler is about to claim, exactly as a
            # concurrent ARM job would.
            highest = original_scalar(statement, *args, **kwargs)
            if (
                str(statement) == str(select(func.max(RuleSet.version)))
                and not rival_versions
            ):
                rival_versions.append((highest or 0) + 1)
                with Session(migrated_engine) as rival, rival.begin():
                    rival.add(
                        RuleSet(
                            rule_set_id=uuid4(),
                            version=rival_versions[0],
                            status="draft",
                            notes="rival",
                        )
                    )
            return highest

        session.scalar = claim_version_first
        rule_set_id = _persist_draft_ruleset(
            session, candidate, terms, notes="retry regression",
            job_id=claimed.job_id, claim_token=claimed.claim_token,
        )

    assert rival_versions, "the rival never committed; the collision was not forced"

    with Session(migrated_engine) as session:
        persisted = session.get(RuleSet, UUID(rule_set_id))
        assert persisted is not None
        # The first attempt lost the race, so the surviving set is the one past it.
        assert persisted.version == rival_versions[0] + 1
        assert persisted.status == "draft"
        assert persisted.notes == "retry regression"
        assert persisted.job_id == claimed.job_id

        # Thresholds are written after the retry loop: they only reach the
        # database if the savepoint rollback left the caller's transaction open.
        threshold_count = session.scalar(
            select(func.count())
            .select_from(RuleSetThreshold)
            .where(RuleSetThreshold.rule_set_id == UUID(rule_set_id))
        )
        assert threshold_count == len(candidate.streams)
        assert len(persisted.extracted_rules) == len(candidate.rules)


def test_committed_publication_survives_parent_crash_and_reclaimed_retry(
    migrated_engine, arm_publication
):
    claimed, candidate, terms = arm_publication
    with Session(migrated_engine) as session, session.begin():
        rule_set_id = _persist_draft_ruleset(
            session, candidate, terms, notes="original publication",
            job_id=claimed.job_id, claim_token=claimed.claim_token,
        )

    # The child committed, but the parent crashed without calling complete().
    with Session(migrated_engine) as session, session.begin():
        before = _stored_publication(session, rule_set_id)
        assert len(before[1]) == len(PatternStream)
        assert len(before[2]) == len(candidate.rules)
        job = JobRepository(session).get(claimed.job_id)
        assert job.status is JobStatus.RUNNING
        assert job.result is None
        session.execute(
            text(
                "UPDATE execution_job SET lease_expires_at = "
                "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
            ),
            {"job_id": claimed.job_id},
        )

    with Session(migrated_engine) as session, session.begin():
        recovered = JobRepository(session).recover_stale()
        assert recovered.requeued == 1 and recovered.failed == 0
        retry = JobRepository(session).claim_next(
            claimed_by="replacement-parent", lease_seconds=300,
            job_types=[JobType.ARM],
        )
        assert retry is not None and retry.job_id == claimed.job_id
        assert retry.attempt_count == 2
        assert retry.claim_token != claimed.claim_token

    # Different input and notes must not overwrite any already-published rows.
    changed = replace(
        candidate,
        streams=tuple(
            replace(
                stream,
                thresholds=replace(stream.thresholds, min_support=Decimal("0.9")),
                rules=(),
            )
            for stream in candidate.streams
        ),
    )
    with Session(migrated_engine) as session, session.begin():
        with pytest.raises(ValueError, match="^ARM publication requires a live worker claim$"):
            _persist_draft_ruleset(
                session, changed, (), notes="stale worker",
                job_id=claimed.job_id, claim_token=claimed.claim_token,
            )
        retried_id = _persist_draft_ruleset(
            session, changed, (), notes="must not replace original",
            job_id=retry.job_id, claim_token=retry.claim_token,
        )
        assert retried_id == rule_set_id
        assert _stored_publication(session, rule_set_id) == before
        completed = JobRepository(session).complete(
            retry.job_id, retry.claim_token, result={"rule_set_id": retried_id}
        )
        assert completed is not None and completed.status is JobStatus.SUCCEEDED

    with Session(migrated_engine) as session:
        assert _stored_publication(session, rule_set_id) == before
        assert session.scalar(
            select(func.count()).select_from(RuleSet)
            .where(RuleSet.job_id == claimed.job_id)
        ) == 1


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "invalid_claim",
    ["wrong_token", "expired", "queued", "succeeded", "failed", "timed_out", "missing", "wrong_kind"],
)
def test_publication_rejects_invalid_claim_even_for_existing_set(
    migrated_engine, arm_publication, existing, invalid_claim
):
    claimed, candidate, terms = arm_publication
    rule_set_id = None
    with Session(migrated_engine) as session, session.begin():
        if existing:
            rule_set_id = _persist_draft_ruleset(
                session, candidate, terms, notes="unchanged",
                job_id=claimed.job_id, claim_token=claimed.claim_token,
            )
        if invalid_claim in {"expired", "queued"}:
            session.execute(
                text(
                    "UPDATE execution_job SET lease_expires_at = "
                    "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
                ),
                {"job_id": claimed.job_id},
            )
            if invalid_claim == "queued":
                JobRepository(session).recover_stale()
        elif invalid_claim == "succeeded":
            assert JobRepository(session).complete(
                claimed.job_id, claimed.claim_token, result={"test": True}
            ) is not None
        elif invalid_claim == "failed":
            assert JobRepository(session).fail(
                claimed.job_id, claimed.claim_token,
                error_code="test_failure", error_message="Synthetic failure.",
            ) is not None
        elif invalid_claim == "timed_out":
            assert JobRepository(session).timeout(
                claimed.job_id, claimed.claim_token
            ) is not None
        elif invalid_claim == "wrong_kind":
            session.execute(
                text("UPDATE execution_job SET job_type = 'ga' WHERE job_id = :job_id"),
                {"job_id": claimed.job_id},
            )
        before_counts = _publication_counts(session)
        before = _stored_publication(session, rule_set_id) if existing else None

    with pytest.raises(ValueError, match="^ARM publication requires a live worker claim$"):
        with Session(migrated_engine) as session, session.begin():
            def reject_publication_lookup(state):
                if state.is_select:
                    assert not any(
                        table.is_derived_from(RuleSet.__table__)
                        for table in state.statement.get_final_froms()
                    ), "invalid claim reached RuleSet lookup before rejection"

            def reject_publication_write(session, *_):
                assert not any(
                    isinstance(item, (RuleSet, RuleSetThreshold, ExtractedRule))
                    for item in session.new
                ), "invalid claim reached publication writes before rejection"

            event.listen(session, "do_orm_execute", reject_publication_lookup)
            event.listen(session, "before_flush", reject_publication_write)
            _persist_draft_ruleset(
                session, candidate, terms, notes="must not publish",
                job_id=uuid4() if invalid_claim == "missing" else claimed.job_id,
                claim_token=uuid4() if invalid_claim == "wrong_token" else claimed.claim_token,
            )

    with Session(migrated_engine) as session:
        assert _publication_counts(session) == before_counts
        if existing:
            assert _stored_publication(session, rule_set_id) == before


def test_lease_expiring_during_final_flush_rolls_back_entire_publication(
    migrated_engine, arm_publication, monkeypatch
):
    claimed, candidate, terms = arm_publication
    terms = (replace(terms[0], academic_year="9001-9002"), *terms[1:])
    source_term_id = uuid4()
    expired = []
    with Session(migrated_engine) as session:
        before = _publication_counts(session)

    with pytest.raises(ValueError, match="^ARM publication requires a live worker claim$"):
        with Session(migrated_engine) as session, session.begin():
            # Insert immutable synthetic provenance in this transaction only;
            # rollback removes it without disabling history's delete guards.
            session.add_all([
                Term(
                    term_id=source_term_id, academic_year=terms[0].academic_year,
                    term_number=1, is_active=False,
                ),
                HistoricalSnapshot(
                    snapshot_id=terms[0].snapshot_id, source_term_id=source_term_id,
                    academic_year=terms[0].academic_year, term_number=1,
                ),
            ])
            session.flush()
            original_flush = session.flush

            def expire_after_final_flush(*args, **kwargs):
                original_flush(*args, **kwargs)
                # The final flush follows included_terms population. Earlier
                # flushes and autoflushes must leave the initial claim live.
                sets = [
                    item for item in session.identity_map.values()
                    if isinstance(item, RuleSet) and "included_terms" in item.__dict__
                ]
                if sets and not expired:
                    expired.append(sets[0].rule_set_id)
                    stored = _stored_publication(session, str(sets[0].rule_set_id))
                    assert len(stored[1]) == len(candidate.streams)
                    assert len(stored[2]) == len(candidate.rules)
                    assert len(stored[3]) == 1
                    session.execute(
                        text(
                            "UPDATE execution_job SET lease_expires_at = "
                            "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
                        ),
                        {"job_id": claimed.job_id},
                    )

            monkeypatch.setattr(session, "flush", expire_after_final_flush)
            _persist_draft_ruleset(
                session, candidate, terms, notes="must roll back",
                job_id=claimed.job_id, claim_token=claimed.claim_token,
            )
            pytest.fail("publication accepted the expired final-flush claim")

    assert expired, "the final-flush expiry hook did not run"
    with Session(migrated_engine) as session:
        assert _publication_counts(session) == before
        assert session.get(RuleSet, expired[0]) is None
        assert session.get(Term, source_term_id) is None
        assert session.get(HistoricalSnapshot, terms[0].snapshot_id) is None


@pytest.mark.parametrize("expire_while_waiting", [False, True])
def test_same_job_publications_serialize_and_recheck_claim_after_lock_wait(
    migrated_engine, arm_publication, monkeypatch, expire_while_waiting
):
    claimed, candidate, terms = arm_publication
    ready = Event()
    contender_pids = []
    if expire_while_waiting:
        monkeypatch.setattr(settings, "job_lease_seconds", 5)

    def publish_again():
        with Session(migrated_engine) as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            contender_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            return _persist_draft_ruleset(
                session, candidate, terms, notes="contender must not replace",
                job_id=claimed.job_id, claim_token=claimed.claim_token,
            )

    with Session(migrated_engine) as first:
        first.begin()
        owner_pid = first.scalar(text("SELECT pg_backend_pid()"))
        rule_set_id = _persist_draft_ruleset(
            first, candidate, terms, notes="first publisher",
            job_id=claimed.job_id, claim_token=claimed.claim_token,
        )
        lease_deadline = first.scalar(
            text("SELECT lease_expires_at FROM execution_job WHERE job_id = :job_id"),
            {"job_id": claimed.job_id},
        )
        before = _stored_publication(first, rule_set_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(publish_again)
            try:
                assert ready.wait(5), "contender did not start"
                deadline = monotonic() + 5
                with Session(migrated_engine) as observer:
                    while monotonic() < deadline:
                        blockers = observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": contender_pids[0]},
                        )
                        if owner_pid in blockers:
                            break
                        if future.done():
                            future.result()
                            pytest.fail("contender returned before the owner committed")
                        sleep(0.01)
                    else:
                        pytest.fail("contender never waited on the publication lock")
                    if expire_while_waiting:
                        # Wait out the renewed publication lease without another
                        # row update while the contender is blocked.
                        assert observer.scalar(
                            text("SELECT clock_timestamp() < :deadline"),
                            {"deadline": lease_deadline},
                        ), "claim expired before lock contention was established"
                        deadline = monotonic() + 6
                        while not observer.scalar(
                            text("SELECT clock_timestamp() >= :deadline"),
                            {"deadline": lease_deadline},
                        ):
                            assert monotonic() < deadline, "database clock did not advance"
                            sleep(0.05)
                first.commit()
                if expire_while_waiting:
                    with pytest.raises(ValueError, match="^ARM publication requires a live worker claim$"):
                        future.result(timeout=10)
                else:
                    assert future.result(timeout=10) == rule_set_id
            finally:
                # Release the row lock even if an assertion fails, before join.
                first.rollback()

    with Session(migrated_engine) as session:
        assert _stored_publication(session, rule_set_id) == before
        assert session.scalar(
            select(func.count()).select_from(RuleSet)
            .where(RuleSet.job_id == claimed.job_id)
        ) == 1


@pytest.mark.parametrize("version", [3, CODEC_VERSION], ids=("legacy", "current"))
def test_run_arm_recovers_committed_envelope_after_parent_crash_without_mining(
    migrated_engine, arm_publication, monkeypatch, version
):
    claimed, candidate, terms = arm_publication
    monkeypatch.setattr(
        "app.db.session.SessionLocal", sessionmaker(bind=migrated_engine)
    )
    payload = {
        "version": version,
        "terms": encode_historical_terms(terms),
        "thresholds": encode_mining_thresholds({
            stream.stream: stream.thresholds for stream in candidate.streams
        }),
        "notes": "real handler path",
        "job_id": str(claimed.job_id),
        "claim_token": str(claimed.claim_token),
    }
    if version == 3:
        for term in payload["terms"]:
            term["semester"] = term.pop("term_number")
    result = run_arm(payload)
    assert result["version"] == CODEC_VERSION
    assert result["metrics"]["stream_count"] == len(PatternStream)
    assert result["metrics"]["retained_rule_count"] == len(candidate.rules)
    assert result["metrics"]["is_empty"] is False
    with Session(migrated_engine) as session, session.begin():
        persisted = session.get(RuleSet, UUID(result["rule_set_id"]))
        assert persisted.job_id == claimed.job_id
        assert persisted.status == "draft"
        assert persisted.notes == payload["notes"]
        assert len(persisted.extracted_rules) == len(candidate.rules)
        before = _stored_publication(session, result["rule_set_id"])
        job = JobRepository(session).get(claimed.job_id)
        assert job.status is JobStatus.RUNNING
        assert persisted.publication_result == result
        assert job.result is None
        # The child committed the artifact/envelope; the parent never completed.
        session.execute(
            text(
                "UPDATE execution_job SET lease_expires_at = "
                "clock_timestamp() - INTERVAL '1 second' WHERE job_id = :job_id"
            ),
            {"job_id": claimed.job_id},
        )

    with Session(migrated_engine) as session, session.begin():
        recovered = JobRepository(session).recover_stale()
        assert recovered.requeued == 1 and recovered.failed == 0
        reclaimed = JobRepository(session).claim_next(
            claimed_by="replacement-arm-parent", lease_seconds=300,
            job_types=[JobType.ARM],
        )
        assert reclaimed is not None and reclaimed.job_id == claimed.job_id
        assert reclaimed.attempt_count == 2
        assert reclaimed.claim_token is not None
        assert reclaimed.claim_token != claimed.claim_token
        assert reclaimed.result is None

    def reject_mining(*args, **kwargs):
        raise AssertionError("committed ARM publication must be recovered before mining")

    monkeypatch.setattr(RuleMiner, "mine", reject_mining)
    with pytest.raises(ValueError, match="^ARM publication requires a live worker claim$"):
        run_arm(payload)
    with Session(migrated_engine) as session:
        assert _stored_publication(session, result["rule_set_id"]) == before
        assert JobRepository(session).get(claimed.job_id) == reclaimed

    retry = run_arm({
        **payload,
        "claim_token": str(reclaimed.claim_token),
        "notes": "must not replace handler publication",
    })
    assert retry == result
    with Session(migrated_engine) as session:
        assert _stored_publication(session, result["rule_set_id"]) == before
        assert JobRepository(session).get(claimed.job_id) == reclaimed
        assert session.scalar(
            select(func.count()).select_from(RuleSet)
            .where(RuleSet.job_id == claimed.job_id)
        ) == 1


@pytest.mark.parametrize("missing_field", ["job_id", "claim_token"])
def test_run_arm_requires_worker_claim_payload(
    migrated_engine, arm_publication, monkeypatch, missing_field
):
    claimed, candidate, terms = arm_publication
    monkeypatch.setattr(
        "app.db.session.SessionLocal", sessionmaker(bind=migrated_engine)
    )
    payload = {
        "version": CODEC_VERSION,
        "terms": encode_historical_terms(terms),
        "thresholds": encode_mining_thresholds({
            stream.stream: stream.thresholds for stream in candidate.streams
        }),
        "job_id": str(claimed.job_id),
        "claim_token": str(claimed.claim_token),
    }
    del payload[missing_field]
    with Session(migrated_engine) as session:
        before = _publication_counts(session)
    with pytest.raises((KeyError, ValueError)):
        run_arm(payload)
    with Session(migrated_engine) as session:
        assert _publication_counts(session) == before


@pytest.mark.parametrize("reader", ["inventory", "detail", "rules", "named", "active"])
def test_ordinary_rule_set_reads_leave_recovery_envelope_unloaded(
    migrated_engine, arm_publication, monkeypatch, reader
):
    claimed, candidate, terms = arm_publication
    monkeypatch.setattr(
        "app.db.session.SessionLocal", sessionmaker(bind=migrated_engine)
    )
    result = run_arm({
        "version": CODEC_VERSION,
        "terms": encode_historical_terms(terms),
        "thresholds": encode_mining_thresholds({
            stream.stream: stream.thresholds for stream in candidate.streams
        }),
        "job_id": str(claimed.job_id),
        "claim_token": str(claimed.claim_token),
    })
    rule_set_id = UUID(result["rule_set_id"])
    if reader == "active":
        with Session(migrated_engine) as session, session.begin():
            session.get(RuleSet, rule_set_id).status = "active"

    with Session(migrated_engine) as session:
        statements, loaded = [], []

        def record_statement(_connection, _cursor, statement, *_):
            statements.append(statement)

        def record_rule_set(_session, instance):
            if isinstance(instance, RuleSet):
                loaded.append(instance)

        event.listen(session.connection(), "before_cursor_execute", record_statement)
        event.listen(session, "loaded_as_persistent", record_rule_set)
        if reader == "inventory":
            assert any(
                item["rule_set_id"] == rule_set_id
                for item in rule_set_service.list_rule_sets(session)
            )
        elif reader == "detail":
            assert rule_set_service.get_rule_set_detail(session, rule_set_id)[
                "rule_set_id"
            ] == rule_set_id
        elif reader == "rules":
            assert rule_set_service.list_rule_set_rules(
                session, rule_set_id, "teacher_subject", limit=1
            )["total"] == 1
        else:
            guidance = DatabaseRuleProvider(session).get_rules(
                None if reader == "active" else rule_set_id
            )
            assert len(guidance.rules) == len(candidate.rules)

        assert loaded
        assert all("publication_result" in inspect(item).unloaded for item in loaded)
        assert statements
        assert all("publication_result" not in statement for statement in statements)
