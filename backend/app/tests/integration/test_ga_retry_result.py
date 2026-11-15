"""CHR-185 / CHR-FIX-015 a retried GA job reports the schedule it reused."""

import pytest
from sqlalchemy import func, select

import app.db.session
from app.models import JobType, Schedule, ScheduleRecord
from app.repositories.jobs import JobRepository
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.services.ga import GAEngine
from app.services.ga.config import GAConfig
from app.services.jobs import encode_ga_payload, run_ga
from app.services.schedules import persist_candidate
from app.tests.integration.test_ga_publication import _expire, ga_publication  # noqa: F401
from app.tests.support.scheduling import TERM_ID

pytestmark = pytest.mark.postgres


def _payload(make_session, claim):
    with make_session() as session:
        problem = SQLAlchemySchedulingProblemProvider(session).get_problem(TERM_ID)
    config = GAConfig(
        population_size=4, max_generations=2, tournament_size=2, elite_count=1,
        random_seed=17, meeting_day_patterns=("MW",),
    )
    return {
        **encode_ga_payload(problem, config),
        "job_id": str(claim.job_id), "claim_token": str(claim.claim_token),
    }


def _reclaim(make_session, claim):
    with make_session() as session:
        _expire(session, claim.job_id)
        repository = JobRepository(session)
        assert repository.recover_stale().requeued == 1
        retry = repository.claim_next(
            claimed_by="retry", lease_seconds=300, job_types=[JobType.GA],
        )
        session.commit()
    assert retry.job_id == claim.job_id and retry.claim_token != claim.claim_token
    return retry


def _published(make_session):
    with make_session() as session:
        return (
            session.execute(select(Schedule.__table__)).all(),
            session.execute(
                select(ScheduleRecord.__table__).order_by(ScheduleRecord.record_id)
            ).all(),
        )


@pytest.fixture
def worker_db(ga_publication, monkeypatch):  # noqa: F811
    make_session, claim, _candidate = ga_publication
    monkeypatch.delenv("GA_SKIP_PERSIST", raising=False)
    monkeypatch.setattr(app.db.session, "SessionLocal", make_session)
    return make_session, claim


def test_retry_after_publication_returns_the_stored_run(worker_db, monkeypatch):
    make_session, claim = worker_db
    first = run_ga(_payload(make_session, claim))
    before = _published(make_session)
    retry = _reclaim(make_session, claim)

    def no_rerun(_engine):
        raise AssertionError("a published job must not rerun the GA")

    monkeypatch.setattr(GAEngine, "run", no_rerun)
    second = run_ga(_payload(make_session, retry))

    # CHR-192: a fresh run carries no reuse keys; the reuse is flagged.
    assert "reused_published_result" not in first
    assert second.pop("reused_published_result") is True
    assert second.pop("published_attempt_processing_seconds") > 0
    assert second == first
    schedules, records = _published(make_session)
    assert (schedules, records) == before
    assert len(schedules) == 1
    assert second["candidate"]["schedule_id"] == str(schedules[0].schedule_id)
    stored = sorted(
        (str(r.class_id), str(r.faculty_id), str(r.time_slot_id), r.room_code, r.meeting_days)
        for r in records
    )
    reported = sorted(
        (a["class_id"], a["faculty_id"], a["time_slot_id"], a["room_code"], a["meeting_days"])
        for a in second["candidate"]["assignments"]
    )
    assert reported == stored and reported
    assert second["metrics"]["assigned_class_count"] == len(records)


def test_reused_result_reports_the_published_attempt_time(worker_db, monkeypatch):
    """CHR-192 / CHR-FIX-021: flagged, with the crashed attempt's own seconds."""
    make_session, claim = worker_db
    ticks = iter([100.0, 135.4])
    monkeypatch.setattr("app.services.jobs.handlers.perf_counter", lambda: next(ticks))
    first = run_ga(_payload(make_session, claim))
    retry = _reclaim(make_session, claim)
    monkeypatch.setattr("app.services.jobs.handlers.perf_counter", lambda: 9999.0)
    second = run_ga(_payload(make_session, retry))

    assert "published_attempt_processing_seconds" not in first
    assert second["reused_published_result"] is True
    assert second["published_attempt_processing_seconds"] == pytest.approx(35.4)
    assert "processing_seconds" not in second


def test_reuse_of_schedule_published_before_the_time_was_stored(worker_db):
    make_session, claim = worker_db
    run_ga(_payload(make_session, claim))
    with make_session() as session:
        schedule = session.execute(select(Schedule)).scalar_one()
        result = dict(schedule.publication_result)
        result.pop("published_attempt_processing_seconds")
        schedule.publication_result = result
        session.commit()
    second = run_ga(_payload(make_session, _reclaim(make_session, claim)))

    assert second["reused_published_result"] is True
    assert second["published_attempt_processing_seconds"] is None


def test_stale_attempt_cannot_read_the_published_result(worker_db):
    make_session, claim = worker_db
    run_ga(_payload(make_session, claim))
    _reclaim(make_session, claim)
    with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
        run_ga(_payload(make_session, claim))


def test_retry_of_schedule_without_stored_result_fails_closed(worker_db, ga_publication):  # noqa: F811
    make_session, claim = worker_db
    _, _, candidate = ga_publication
    with make_session() as session:
        persist_candidate(session, candidate, claim_token=claim.claim_token)
    before = _published(make_session)
    retry = _reclaim(make_session, claim)
    with pytest.raises(ValueError, match="GA publication result is unavailable"):
        run_ga(_payload(make_session, retry))
    assert _published(make_session) == before
    with make_session() as session:
        assert session.scalar(select(func.count()).select_from(Schedule)) == 1
