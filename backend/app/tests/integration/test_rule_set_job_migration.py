"""CHR-126 / CHR-ARM-006 PostgreSQL provenance migration and constraints."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AppUser,
    ExecutionJob,
    ExtractedRule,
    Faculty,
    RuleSet,
    RuleSetThreshold,
    Term,
    UserRole,
    rule_set_term,
)
from app.models.extracted_rule import PATTERN_TYPES
from app.tests.integration.alembic_support import run_alembic

pytestmark = pytest.mark.postgres

NEW_REVISION = "c6e8a0b2d4f7"


@pytest.mark.parametrize(
    "old_revision", ["e5b7d9f1a3c6", "f6c8e0a2b4d7", "b8e1f3a5c7d9"]
)
def test_upgrade_preserves_legacy_rule_sets(postgres_test_url: str, old_revision: str):
    # The shared URL fixture permits only a dedicated database ending in _test.
    run_alembic(postgres_test_url, "downgrade", "base")
    run_alembic(postgres_test_url, "upgrade", old_revision)
    engine = create_engine(postgres_test_url)
    tables = ("rule_set", "rule_set_threshold", "extracted_rule", "rule_set_term")
    generated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(
                Faculty.__table__.insert(),
                dict(
                    faculty_id=UUID(int=1),
                    full_name="<TBD>",
                    is_full_time=True,
                    availability_mask="0" * 48,
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
            for version, status in enumerate(("draft", "active", "superseded"), 1):
                rule_set_id = UUID(int=10 + version)
                connection.execute(
                    text(
                        "INSERT INTO rule_set (rule_set_id, version, status, "
                        "generated_at, activated_at, notes) VALUES "
                        "(:id, :version, :status, :generated_at, :activated_at, :notes)"
                    ),
                    dict(
                        id=rule_set_id,
                        version=version,
                        status=status,
                        generated_at=generated_at,
                        activated_at=None if status == "draft" else generated_at,
                        notes="<TBD>",
                    ),
                )
                connection.execute(
                    RuleSetThreshold.__table__.insert(),
                    [
                        dict(
                            threshold_id=UUID(int=100 * version + stream),
                            rule_set_id=rule_set_id,
                            pattern_type=pattern_type,
                            min_support="0.1000",
                            min_confidence="0.7000",
                            min_lift="1.2000",
                            min_term_recurrence=2,
                            earliest_academic_year="2023-2024",
                        )
                        for stream, pattern_type in enumerate(PATTERN_TYPES)
                    ],
                )
                connection.execute(
                    ExtractedRule.__table__.insert(),
                    dict(
                        rule_id=UUID(int=2000 + version),
                        rule_set_id=rule_set_id,
                        pattern_type="teacher_day",
                        faculty_id=UUID(int=1),
                        meeting_days="MWF",
                        support="0.2500",
                        confidence="0.8000",
                        lift="1.5000",
                        term_recurrence=3,
                        guidance_score="0.6000",
                    ),
                )
                connection.execute(
                    rule_set_term.insert(),
                    dict(rule_set_id=rule_set_id, term_id=UUID(int=2)),
                )
            before = {
                table: connection.execute(
                    text(f"SELECT * FROM {table} ORDER BY 1, 2")
                ).mappings().all()
                for table in tables
            }

        run_alembic(postgres_test_url, "upgrade", NEW_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == NEW_REVISION
            for table in tables:
                rows = connection.execute(
                    text(f"SELECT * FROM {table} ORDER BY 1, 2")
                ).mappings().all()
                if table == "rule_set":
                    assert all(row["job_id"] is None for row in rows)
                    assert all(row["publication_result"] is None for row in rows)
                    rows = [
                        {key: value for key, value in row.items()
                         if key in before[table][0]}
                        for row in rows
                    ]
                assert rows == before[table]
            index_definition = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = 'uq_rule_set_job_id'"
                )
            )
            assert "UNIQUE INDEX" in index_definition
            assert "WHERE (job_id IS NOT NULL)" in index_definition
    finally:
        engine.dispose()
        # Restore a clean head so committed migration fixtures cannot leak.
        run_alembic(postgres_test_url, "downgrade", "base")
        run_alembic(postgres_test_url, "upgrade", "head")


def test_rule_set_job_constraints(db_session: Session):
    user = AppUser(
        user_id=UUID(int=1),
        username="rule-set-job-test",
        password_hash="$argon2id$test-only-placeholder",
        role=UserRole.DEPARTMENT_ADMINISTRATOR,
    )
    db_session.add(user)
    db_session.flush()
    job = ExecutionJob(
        job_id=UUID(int=2),
        job_type="arm",
        requested_by=user.user_id,
        request={},
        timeout_seconds=120,
        max_attempts=1,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add_all(
        [
            RuleSet(rule_set_id=UUID(int=3), version=1, status="draft", job_id=job.job_id),
            RuleSet(rule_set_id=UUID(int=4), version=2, status="draft", job_id=None),
            RuleSet(rule_set_id=UUID(int=5), version=3, status="draft"),
        ]
    )
    db_session.flush()
    assert db_session.scalars(
        select(RuleSet.job_id).order_by(RuleSet.version)
    ).all() == [job.job_id, None, None]

    for job_id, constraint in (
        (job.job_id, "uq_rule_set_job_id"),
        (UUID(int=999), "fk_rule_set_job_id"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            with db_session.begin_nested():
                db_session.add(
                    RuleSet(
                        rule_set_id=UUID(int=6), version=4, status="draft", job_id=job_id
                    )
                )
                db_session.flush()

    with pytest.raises(IntegrityError, match="fk_rule_set_job_id"):
        with db_session.begin_nested():
            db_session.execute(
                delete(ExecutionJob).where(ExecutionJob.job_id == job.job_id)
            )
    assert db_session.get(RuleSet, UUID(int=3)).job_id == job.job_id
    assert db_session.scalar(
        select(ExecutionJob.job_id).where(ExecutionJob.job_id == job.job_id)
    ) == job.job_id
