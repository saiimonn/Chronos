"""CHR-129 / CHR-ARM-008 mining provenance and summaries on rule versions."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AppUser, ExtractedRule, HistoricalRecord, JobType, RuleSet, UserRole
from app.repositories.jobs import JobRepository
from app.services.arm import (
    MiningMode,
    MiningThresholds,
    PatternStream,
    RuleMiner,
    load_historical_terms_by_ids,
    prepare_training_data,
)
from app.services.jobs.handlers import _persist_draft_ruleset
from app.tests.support.history import seed_historical_terms

pytestmark = pytest.mark.postgres

PERMISSIVE = {
    stream: MiningThresholds(
        min_support=Decimal("0.1"),
        min_confidence=Decimal("0.1"),
        min_lift=Decimal("0"),
        min_term_recurrence=1,
    )
    for stream in PatternStream
}


def _mine(db_session: Session, snapshot_ids):
    terms = load_historical_terms_by_ids(db_session, snapshot_ids)
    candidate = RuleMiner().mine(
        prepare_training_data(terms, mode=MiningMode.OPERATIONAL), PERMISSIVE
    )
    return candidate, terms


@pytest.fixture
def publication_claim(db_session: Session):
    user = AppUser(
        username=f"provenance-{uuid4()}",
        password_hash="$argon2id$test-only-placeholder",
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
    )
    db_session.add(user)
    db_session.flush()
    repository = JobRepository(db_session)
    job = repository.enqueue(
        job_type=JobType.ARM,
        requested_by=user.user_id,
        request={},
        timeout_seconds=120,
    )
    claim = repository.claim_next(
        claimed_by="provenance-test", lease_seconds=300, job_types=[JobType.ARM]
    )
    assert claim is not None and claim.job_id == job.job_id
    return claim


def test_a_version_records_its_run_snapshots_and_what_filtering_kept(
    db_session: Session, publication_claim,
):
    snapshot_ids = seed_historical_terms(db_session)
    seeded = db_session.scalars(
        select(HistoricalRecord).where(HistoricalRecord.snapshot_id == snapshot_ids[0])
    ).one()
    # The same habit once in a room that has since left the catalog: its room
    # rule is mined but cannot be published.
    db_session.add(
        HistoricalRecord(
            snapshot_id=snapshot_ids[-1],
            source_faculty_id=seeded.source_faculty_id,
            faculty_name=seeded.faculty_name,
            subject_code=seeded.subject_code,
            subject_title=seeded.subject_title,
            room_code="GONE",
            meeting_days="MWF",
            time_slot="07:30-08:30",
        )
    )
    db_session.flush()
    candidate, terms = _mine(db_session, snapshot_ids)
    rule_set_id = _persist_draft_ruleset(
        db_session,
        candidate,
        terms,
        notes=None,
        job_id=publication_claim.job_id,
        claim_token=publication_claim.claim_token,
        requested_by_user_id=publication_claim.requested_by,
    )

    rule_set = db_session.get(RuleSet, UUID(rule_set_id))
    assert rule_set.job_id == publication_claim.job_id
    assert (rule_set.source_job_id, rule_set.requested_by_user_id) == (
        publication_claim.job_id, publication_claim.requested_by
    )
    assert {snapshot.snapshot_id for snapshot in rule_set.source_snapshots} == set(
        snapshot_ids
    )
    assert sorted(
        (snapshot.academic_year, snapshot.term_number)
        for snapshot in rule_set.source_snapshots
    ) == [("2023-2024", 1), ("2023-2024", 2), ("2024-2025", 1), ("2024-2025", 2)]

    persisted = db_session.scalar(
        select(func.count())
        .select_from(ExtractedRule)
        .where(ExtractedRule.rule_set_id == rule_set.rule_set_id)
    )
    summary = rule_set.mining_summary
    assert summary["input_record_count"] == 5
    assert summary["persisted_rule_count"] == persisted
    assert summary["mined_rule_count"] == persisted + 1
    room = next(
        item for item in summary["streams"] if item["stream"] == "teacher_subject_room"
    )
    assert room["mined_rule_count"] == 2
    assert room["persisted_rule_count"] == 1
    assert room["excluded_rule_counts"] == {
        "unknown_faculty": 0,
        "unknown_subject": 0,
        "unknown_room": 1,
    }
    assert all(item["transaction_count"] > 0 for item in summary["streams"])


def test_a_run_whose_filtering_leaves_no_rule_is_refused(
    db_session: Session, publication_claim
):
    snapshot_ids = seed_historical_terms(db_session)
    candidate, terms = _mine(db_session, snapshot_ids)
    # Every mined rule names an instructor who has since left the catalog.
    candidate = replace(
        candidate,
        streams=tuple(
            replace(
                stream,
                rules=tuple(replace(rule, faculty_id=uuid4()) for rule in stream.rules),
            )
            for stream in candidate.streams
        ),
    )
    assert candidate.rules
    before = db_session.scalar(select(func.count()).select_from(RuleSet))

    with pytest.raises(ValueError, match="catalog filtering left no usable rules"):
        with db_session.begin_nested():
            _persist_draft_ruleset(
                db_session, candidate, terms, notes=None,
                job_id=publication_claim.job_id,
                claim_token=publication_claim.claim_token,
            )

    assert db_session.scalar(select(func.count()).select_from(RuleSet)) == before
