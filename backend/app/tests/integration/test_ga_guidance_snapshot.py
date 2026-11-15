"""CHR-113 / CHR-GA-004 synthetic PostgreSQL guidance snapshot boundaries."""

import logging
from decimal import Decimal
from threading import Event, Thread
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain import Assignment
from app.models import (
    ExecutionJob,
    ExtractedRule,
    JobStatus,
    RuleSet,
    RuleSetThreshold,
    Term,
    UserRole,
)
from app.repositories.jobs import JobRepository
from app.schemas.jobs import GAConfigRequest, SubmitGAJobRequest
from app.services.arm.providers import DatabaseRuleProvider
from app.services.ga.rule_scoring import SCORING_POLICY, RuleBackedHistoricalScorer
from app.services.job_submission import GAGuidanceError, JobSubmissionService
from app.services.jobs.codecs import (
    CODEC_VERSION,
    decode_block_generation_context,
    decode_guidance_snapshot,
    decode_scheduling_problem,
    encode_arm_payload,
    encode_block_ga_payload,
    encode_ga_payload,
)
from app.services.rule_sets import activate_rule_set
from app.tests.integration.alembic_support import run_alembic
from app.tests.integration.test_block_generation_context_repository import (
    OTHER_BLOCK_ID,
    OTHER_CLASS_ID,
    THIRD_CLASS_ID,
    seed_base_schedule,
    seed_second_block,
)
from app.tests.support.api import make_user
from app.tests.support.scheduling import (
    BLOCK_ID,
    CLASS_ID,
    FACULTY_ID,
    SCHEDULE_ID,
    TERM_ID,
    TIME_SLOT_ID,
    seed_scheduling_problem,
)

pytestmark = pytest.mark.postgres

ACTOR_ID = UUID("11300000-0000-0000-0000-000000000001")
ACTIVE_ID = UUID("11300000-0000-0000-0000-000000000010")
DRAFT_ID = UUID("11300000-0000-0000-0000-000000000020")
SOURCE_TERM_ID = UUID("11300000-0000-0000-0000-000000000030")
RULE_SHAPES = {
    "teacher_subject": {"subject_code": "<SUBJECT-1>"},
    "teacher_day": {"meeting_days": "MWF"},
    "teacher_time": {"time_slot": "<TIME-SLOT-1>"},
    "teacher_subject_room": {"subject_code": "<SUBJECT-1>", "room_code": "<ROOM-1>"},
}
ACTIVE_SCORES = {
    "teacher_subject": "0.8000",
    "teacher_day": "0.6000",
    "teacher_time": "0.4000",
    "teacher_subject_room": "0.2000",
}


def _seed_inputs(session: Session) -> UUID:
    seed_scheduling_problem(session)
    seed_second_block(session, ready_for_generation=True)
    seed_base_schedule(session)
    actor = make_user(
        session,
        username="<GUIDANCE-ACTOR>",
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
    )
    actor.user_id = ACTOR_ID
    session.flush()
    return actor.user_id


def _seed_rule_sets(session: Session, *, active: bool = True) -> None:
    source_term = Term(
        term_id=SOURCE_TERM_ID,
        academic_year="<YEAR-0>",
        term_number=1,
        is_active=False,
    )
    session.add(source_term)
    for version, rule_set_id in ((1, ACTIVE_ID), (2, DRAFT_ID)):
        rule_set = RuleSet(
            rule_set_id=rule_set_id,
            version=version,
            status="active" if active and version == 1 else "draft",
            included_terms=[source_term],
        )
        session.add(rule_set)
        session.flush()
        for index, (stream, shape) in enumerate(RULE_SHAPES.items(), start=1):
            rule_set.thresholds.append(
                RuleSetThreshold(
                    threshold_id=UUID(
                        f"11300000-0000-0000-0000-{version * 100 + index:012d}"
                    ),
                    pattern_type=stream,
                    min_support=Decimal("0.1000"),
                    min_confidence=Decimal("0.1000"),
                    min_lift=Decimal("0.0000"),
                    min_term_recurrence=1,
                    earliest_academic_year="<YEAR-1>",
                )
            )
            rule_set.extracted_rules.append(
                ExtractedRule(
                    rule_id=UUID(
                        f"11300000-0000-0000-0000-{version * 1000 + index:012d}"
                    ),
                    pattern_type=stream,
                    faculty_id=FACULTY_ID,
                    **shape,
                    guidance_score=Decimal(
                        ACTIVE_SCORES[stream] if version == 1 else "0.1000"
                    ),
                    support=Decimal("0.1250" if version == 1 else "0.9000"),
                    confidence=Decimal("0.7500" if version == 1 else "0.9500"),
                    lift=Decimal("2.5000" if version == 1 else "1.0500"),
                    term_recurrence=3 if version == 1 else 9,
                )
            )
    session.flush()


def _service(session: Session) -> JobSubmissionService:
    return JobSubmissionService(
        session,
        JobRepository(session),
        encode_ga_payload,
        encode_arm_payload,
        encode_block_ga_payload,
        max_attempts=1,
        ga_timeout_seconds=30,
        arm_timeout_seconds=30,
    )


def _requests() -> tuple[SubmitGAJobRequest, SubmitGAJobRequest]:
    config = GAConfigRequest(random_seed=17)
    return (
        SubmitGAJobRequest(term_id=TERM_ID, config=config),
        SubmitGAJobRequest(
            term_id=TERM_ID,
            block_id=BLOCK_ID,
            base_schedule_id=SCHEDULE_ID,
            config=config,
        ),
    )


def test_submission_freezes_active_rules_and_scoped_inputs_across_activation(
    db_session: Session,
):
    actor_id = _seed_inputs(db_session)
    _seed_rule_sets(db_session)
    service = _service(db_session)
    frozen_guidance = DatabaseRuleProvider(db_session).get_rules()
    queued = []

    for request in _requests():
        accepted = service.submit_ga(request, actor_id)
        db_session.expire_all()
        stored = JobRepository(db_session).get(accepted.job_id)
        assert stored is not None
        assert stored.status is JobStatus.QUEUED
        assert stored.requested_by == actor_id
        assert stored.request["job_id"] == str(accepted.job_id)
        assert stored.request["version"] == CODEC_VERSION == 7
        assert stored.request["config"]["random_seed"] == 17
        snapshot = stored.request["historical_guidance"]
        assert snapshot["mode"] == "guided"
        assert snapshot["rule_set_id"] == str(ACTIVE_ID)
        assert snapshot["rule_set_version"] == 1
        assert (
            snapshot["scoring_policy"]
            == SCORING_POLICY
            == "equal_applicable_streams_v1"
        )
        assert len(snapshot["rules"]) == 4
        assert {rule["stream"] for rule in snapshot["rules"]} == set(RULE_SHAPES)
        for rule in snapshot["rules"]:
            assert rule == {
                "stream": rule["stream"],
                "faculty_id": str(FACULTY_ID),
                **dict.fromkeys(
                    ("subject_code", "room_code", "meeting_days", "time_slot")
                ),
                **RULE_SHAPES[rule["stream"]],
                "guidance_score": ACTIVE_SCORES[rule["stream"]],
                "support": "0.1250",
                "confidence": "0.7500",
                "lift": "2.5000",
                "term_recurrence": 3,
            }
        assert decode_guidance_snapshot(stored.request) == frozen_guidance
        queued.append(stored)

    term_payload, block_payload = (job.request for job in queued)
    assert "block_context" not in term_payload
    assert "problem" not in block_payload
    problem = decode_scheduling_problem(term_payload["problem"])
    context = decode_block_generation_context(block_payload["block_context"])
    assert problem.term.term_id == context.term.term_id == TERM_ID
    assert {item.class_id for item in problem.classes} == {
        CLASS_ID,
        OTHER_CLASS_ID,
        THIRD_CLASS_ID,
    }
    assert context.selected_block_id == BLOCK_ID
    assert context.base_schedule_id == SCHEDULE_ID
    assert [item.class_id for item in context.selected_problem.classes] == [CLASS_ID]
    assert {item.class_id for item in context.locked_assignments} == {
        OTHER_CLASS_ID,
        THIRD_CLASS_ID,
    }
    assert {item.block_id for item in context.locked_assignments} == {OTHER_BLOCK_ID}

    selected = Assignment(
        class_id=CLASS_ID,
        faculty_id=FACULTY_ID,
        time_slot_id=TIME_SLOT_ID,
        room_code="<ROOM-1>",
        meeting_days="MWF",
    )
    locked = tuple(
        Assignment(
            class_id=item.class_id,
            faculty_id=item.faculty_id,
            time_slot_id=item.time_slot_id,
            room_code=item.room_code,
            meeting_days=item.meeting_days,
        )
        for item in context.locked_assignments
    )
    scorer = RuleBackedHistoricalScorer(decode_guidance_snapshot(block_payload))
    block_score = scorer.score(context.selected_problem, (selected,))
    assert block_score.teacher_subject_score == pytest.approx(0.8)
    assert block_score.teacher_day_score == pytest.approx(0.6)
    assert block_score.teacher_time_score == pytest.approx(0.4)
    assert block_score.teacher_subject_room_score == pytest.approx(0.2)
    assert block_score.historical_pattern_attainment == pytest.approx(0.5)
    assert scorer.score(
        problem, (selected, *locked)
    ).historical_pattern_attainment == pytest.approx(0.4)

    activated = activate_rule_set(db_session, DRAFT_ID, actor_id=actor_id)
    assert activated["status"] == "active"
    assert db_session.get(RuleSet, ACTIVE_ID).status == "superseded"
    db_session.execute(
        update(ExtractedRule)
        .where(ExtractedRule.rule_set_id == ACTIVE_ID)
        .values(guidance_score=Decimal("0.0000"), support=Decimal("0.5000"))
    )
    db_session.commit()
    live_guidance = DatabaseRuleProvider(db_session).get_rules()
    assert live_guidance.rule_set_id == DRAFT_ID
    assert live_guidance.version == 2
    assert {rule.guidance_score for rule in live_guidance.rules} == {Decimal("0.1000")}
    assert DatabaseRuleProvider(db_session).get_rules(ACTIVE_ID) != frozen_guidance

    # Read JSONB afresh, not an ORM identity-map value or the enqueue return value.
    for job in queued:
        reloaded = db_session.scalar(
            select(ExecutionJob.request).where(ExecutionJob.job_id == job.job_id)
        )
        assert reloaded == job.request
        assert decode_guidance_snapshot(reloaded) == frozen_guidance


def test_draft_only_submissions_are_explicitly_unguided_in_both_scopes(
    db_session: Session,
):
    actor_id = _seed_inputs(db_session)
    _seed_rule_sets(db_session, active=False)
    service = _service(db_session)

    assert (
        db_session.scalar(select(RuleSet.rule_set_id).where(RuleSet.status == "active"))
        is None
    )
    for request in _requests():
        accepted = service.submit_ga(request, actor_id)
        payload = db_session.scalar(
            select(ExecutionJob.request).where(ExecutionJob.job_id == accepted.job_id)
        )
        assert payload["version"] == CODEC_VERSION == 7
        assert payload["job_id"] == str(accepted.job_id)
        assert payload["historical_guidance"] == {
            "mode": "unguided",
            "scoring_policy": SCORING_POLICY,
            "rule_set_id": None,
            "rule_set_version": None,
            "rules": [],
        }
        assert decode_guidance_snapshot(payload) is None


@pytest.fixture
def guidance_lock_engine(migrated_engine):
    """Own every committed row; never redirect a worker's runtime connection."""
    assert migrated_engine.url.get_backend_name() == "postgresql"
    database_name = migrated_engine.url.database
    assert database_name is not None and database_name.endswith("_test")
    name = f"chronos_ga_guidance_{uuid4().hex}_test"
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


def test_active_rule_set_read_lock_blocks_activation_through_snapshot_and_enqueue(
    guidance_lock_engine,
):
    engine = guidance_lock_engine
    with Session(engine) as seed:
        actor_id = _seed_inputs(seed)
        _seed_rule_sets(seed)
        seed.commit()

    blocked_at = []

    def attempt_activation(
        _connection, _cursor, statement, _parameters, _context, _many
    ):
        sql = statement.lstrip().lower()
        if sql.startswith("select") and "from extracted_rule" in sql:
            boundary = "snapshot"
        elif sql.startswith("insert into execution_job"):
            boundary = "enqueue"
        else:
            return

        # Another reader must succeed: an exclusive lock is not the contract.
        with Session(engine) as reader:
            assert (
                reader.scalar(
                    select(RuleSet.rule_set_id)
                    .where(RuleSet.status == "active")
                    .with_for_update(read=True, nowait=True)
                )
                == ACTIVE_ID
            )

        # Separate committed fixtures make the submission's read lock the
        # only blocker. A timeout bounds the real competing transaction.
        with Session(engine) as rival:
            rival.execute(text("SET LOCAL lock_timeout = '250ms'"))
            rival.execute(text("SET LOCAL statement_timeout = '5s'"))
            with pytest.raises(OperationalError) as error:
                activate_rule_set(rival, DRAFT_ID, actor_id=actor_id)
            sqlstate = getattr(error.value.orig, "sqlstate", None) or getattr(
                error.value.orig, "pgcode", None
            )
            assert sqlstate == "55P03"
            rival.rollback()
        blocked_at.append(boundary)

    accepted_jobs = []
    for request in _requests():
        with Session(engine) as submission:
            connection = submission.connection()
            event.listen(connection, "after_cursor_execute", attempt_activation)
            try:
                accepted_jobs.append(_service(submission).submit_ga(request, actor_id))
            finally:
                event.remove(connection, "after_cursor_execute", attempt_activation)

    assert blocked_at == ["snapshot", "enqueue"] * 2
    with Session(engine) as rival:
        assert rival.get(RuleSet, ACTIVE_ID).status == "active"
        assert rival.get(RuleSet, DRAFT_ID).status == "draft"
        rival.execute(text("SET LOCAL lock_timeout = '1s'"))
        rival.execute(text("SET LOCAL statement_timeout = '5s'"))
        activated = activate_rule_set(rival, DRAFT_ID, actor_id=actor_id)
        assert activated["status"] == "active"

    with Session(engine) as observer:
        for accepted in accepted_jobs:
            stored = JobRepository(observer).get(accepted.job_id)
            assert stored is not None
            assert stored.status is JobStatus.QUEUED
            assert stored.request == {
                **accepted.request,
                "job_id": str(accepted.job_id),
            }
            frozen = decode_guidance_snapshot(stored.request)
            assert frozen.rule_set_id == ACTIVE_ID
            assert frozen.version == 1
            assert len(frozen.rules) == 4
        assert DatabaseRuleProvider(observer).get_rules().rule_set_id == DRAFT_ID


@pytest.mark.parametrize("ga_request", _requests(), ids=("term", "block"))
def test_activation_first_submission_retries_the_replacement_guidance(
    guidance_lock_engine,
    ga_request: SubmitGAJobRequest,
):
    engine = guidance_lock_engine
    with Session(engine) as seed:
        actor_id = _seed_inputs(seed)
        _seed_rule_sets(seed)
        seed.commit()

    shared_query_started = Event()
    capture_finished = Event()
    capture_pids = []
    captured_jobs = []
    capture_errors = []
    active_reads = []

    def before_capture(_connection, _cursor, statement, _parameters, _context, _many):
        sql = " ".join(statement.lower().split())
        if "where rule_set.status =" in sql and "for share" in sql:
            shared_query_started.set()

    def after_capture(_connection, cursor, statement, _parameters, _context, _many):
        sql = " ".join(statement.lower().split())
        if sql.startswith("select") and "where rule_set.status =" in sql:
            kind = "shared" if "for share" in sql else "ordinary"
            active_reads.append((kind, cursor.rowcount))

    def submit_in_thread():
        try:
            with Session(engine) as capture:
                connection = capture.connection(
                    execution_options={"isolation_level": "READ COMMITTED"}
                )
                capture.execute(text("SET LOCAL statement_timeout = '10s'"))
                capture_pids.append(capture.scalar(text("SELECT pg_backend_pid()")))
                event.listen(connection, "before_cursor_execute", before_capture)
                event.listen(connection, "after_cursor_execute", after_capture)
                try:
                    captured_jobs.append(
                        _service(capture).submit_ga(ga_request, actor_id)
                    )
                finally:
                    event.remove(connection, "before_cursor_execute", before_capture)
                    event.remove(connection, "after_cursor_execute", after_capture)
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Guidance submission failed in concurrency test"
            )
            capture_errors.append(error)
        finally:
            capture_finished.set()

    worker = Thread(target=submit_in_thread, name="guidance-capture", daemon=True)
    with Session(engine) as activation:
        activation.connection(execution_options={"isolation_level": "READ COMMITTED"})
        activation.execute(text("SET LOCAL statement_timeout = '10s'"))
        activation_pid = activation.scalar(text("SELECT pg_backend_pid()"))
        assert (
            activation.scalar(
                select(RuleSet.rule_set_id)
                .where(RuleSet.rule_set_id == ACTIVE_ID)
                .with_for_update()
            )
            == ACTIVE_ID
        )
        worker.start()
        try:
            assert shared_query_started.wait(5), "capture never reached FOR SHARE"
            assert capture_pids[0] != activation_pid
            deadline = monotonic() + 5
            # A before-execute event alone could release A before SQL reaches
            # PostgreSQL. Observe the actual lock wait before committing B.
            while not activation.scalar(
                text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
                {"blocker": activation_pid, "waiter": capture_pids[0]},
            ):
                assert monotonic() < deadline, "capture did not block on activation"
                assert not capture_finished.wait(0.01), "capture ended before blocking"
            activated = activate_rule_set(activation, DRAFT_ID, actor_id=actor_id)
            assert activated["status"] == "active"
        finally:
            activation.rollback()
            worker.join(timeout=10)
            assert not worker.is_alive(), "capture thread did not finish"

    if capture_errors:
        raise capture_errors[0]
    assert active_reads == [("shared", 0), ("ordinary", 1), ("shared", 1)]
    assert len(captured_jobs) == 1
    with Session(engine) as observer:
        assert observer.get(RuleSet, ACTIVE_ID).status == "superseded"
        assert observer.get(RuleSet, DRAFT_ID).status == "active"
        stored = JobRepository(observer).get(captured_jobs[0].job_id)
        assert stored is not None
        assert stored.status is JobStatus.QUEUED
        assert stored.request["version"] == CODEC_VERSION == 7
        assert stored.request["job_id"] == str(stored.job_id)
        snapshot = stored.request["historical_guidance"]
        assert snapshot["mode"] == "guided"
        assert snapshot["rule_set_id"] == str(DRAFT_ID)
        assert snapshot["rule_set_version"] == 2
        assert snapshot["scoring_policy"] == SCORING_POLICY
        assert len(snapshot["rules"]) == 4
        for rule in snapshot["rules"]:
            assert {
                key: rule[key]
                for key in (
                    "guidance_score",
                    "support",
                    "confidence",
                    "lift",
                    "term_recurrence",
                )
            } == {
                "guidance_score": "0.1000",
                "support": "0.9000",
                "confidence": "0.9500",
                "lift": "1.0500",
                "term_recurrence": 9,
            }
        assert (
            decode_guidance_snapshot(stored.request)
            == DatabaseRuleProvider(observer).get_rules()
        )


@pytest.mark.parametrize("ga_request", _requests(), ids=("term", "block"))
def test_invalid_active_guidance_is_rejected_before_enqueue(
    db_session: Session,
    ga_request: SubmitGAJobRequest,
):
    actor_id = _seed_inputs(db_session)
    _seed_rule_sets(db_session)
    invalid_days = "   "
    db_session.execute(
        update(ExtractedRule)
        .where(
            ExtractedRule.rule_set_id == ACTIVE_ID,
            ExtractedRule.pattern_type == "teacher_day",
        )
        .values(meeting_days=invalid_days)
    )
    db_session.expire_all()
    source = DatabaseRuleProvider(db_session).get_rules()
    assert source.rule_set_id == ACTIVE_ID
    assert source.version == 1
    day_rule = next(rule for rule in source.rules if rule.stream.value == "teacher_day")
    assert day_rule.meeting_days == invalid_days
    assert (
        str(day_rule.guidance_score),
        str(day_rule.support),
        str(day_rule.confidence),
        str(day_rule.lift),
    ) == ("0.6000", "0.1250", "0.7500", "2.5000")
    job_count = select(func.count()).select_from(ExecutionJob)
    assert db_session.scalar(job_count) == 0

    with pytest.raises(GAGuidanceError) as error:
        _service(db_session).submit_ga(ga_request, actor_id)

    assert error.value.code == "RULE_SET_INVALID"
    assert str(error.value) == (
        "Active historical guidance is invalid; an administrator must review "
        "it before generation."
    )
    assert str(error.value.__cause__) == "invalid historical guidance snapshot"
    assert db_session.scalar(job_count) == 0
