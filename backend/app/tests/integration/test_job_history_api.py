"""CHR-174 / CHR-SCH-014 GA/ARM job history browser API."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppUser, ExecutionJob, UserRole
from app.tests.support.api import make_api_client, make_client

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TERM_A = uuid4()
TERM_B = uuid4()
BLOCK = uuid4()
CONFIG = {"population_size": 10, "random_seed": 42, "max_generations": 5}


def _term(term_id: UUID) -> dict:
    return {"term_id": str(term_id), "academic_year": "2025-2026", "term_number": 1, "is_active": True}


def _ga_request(term_id: UUID, *, block: bool = False) -> dict:
    # Codec 6 payloads ran unguided; the raw problem stands in for sensitive input.
    secret = {"faculty": [{"full_name": "Sensitive Name"}]}
    if block:
        return {
            "version": 6,
            "block_context": {"context": {"term": _term(term_id), "selected_block_id": str(BLOCK), **secret}},
            "config": CONFIG,
        }
    return {"version": 6, "problem": {"term": _term(term_id), **secret}, "config": CONFIG}


def _add(db: Session, user: AppUser, minute: int, kind: str = "ga", status: str = "queued", request=None) -> ExecutionJob:
    created = T0 + timedelta(minutes=minute)
    fields: dict = {}
    if status != "queued":
        fields["started_at"] = created + timedelta(seconds=1)
    if status == "running":
        fields.update(claimed_by="worker", claim_token=uuid4(), lease_expires_at=created + timedelta(hours=1))
    if status in ("succeeded", "failed", "timed_out"):
        fields["finished_at"] = created + timedelta(seconds=2)
    if status == "succeeded":
        fields["result"] = {"candidate": {"schedule_id": None, "assignments": []}, "metrics": {"random_seed": 42}}
    if status == "failed":
        fields.update(error_code="ga_infeasible", error_message="No feasible schedule.")
    if status == "timed_out":
        fields.update(error_code="execution_timed_out", error_message="Execution exceeded its configured timeout.")
    job = ExecutionJob(
        job_type=kind,
        status=status,
        requested_by=user.user_id,
        request=request if request is not None else _ga_request(TERM_A),
        timeout_seconds=60,
        max_attempts=1,
        attempt_count=0 if status == "queued" else 1,
        created_at=created,
        updated_at=created + timedelta(seconds=3),
        **fields,
    )
    db.add(job)
    db.flush()
    return job


def _clients(db: Session):
    committee = make_api_client(db)
    admin = make_api_client(db, role=UserRole.DEPARTMENT_ADMINISTRATOR, username="administrator")
    other = make_api_client(db, username="other-scheduler")
    users = {u.username: u for u in db.scalars(select(AppUser)).all()}
    return committee, admin, other, users


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["job_id"] for item in response.json()["items"]]


def test_history_requires_authentication(db_session: Session):
    assert make_client(db_session).get("/api/v1/jobs").status_code == 401


def test_history_is_role_scoped(db_session: Session):
    committee, admin, _, users = _clients(db_session)
    own = _add(db_session, users["scheduler"], 1)
    other = _add(db_session, users["other-scheduler"], 2)
    arm = _add(db_session, users["administrator"], 3, kind="arm", request={"terms": ["raw"]})
    admin_ga = _add(db_session, users["administrator"], 4)

    assert _ids(committee.get("/api/v1/jobs")) == [str(own.job_id)]
    assert _ids(admin.get("/api/v1/jobs")) == [
        str(job.job_id) for job in (admin_ga, arm, other, own)
    ]
    assert committee.get("/api/v1/jobs?kind=arm").status_code == 403
    assert _ids(admin.get("/api/v1/jobs?kind=arm")) == [str(arm.job_id)]
    # The detail view keeps the same boundary.
    assert committee.get(f"/api/v1/jobs/{other.job_id}").status_code == 404


def test_history_paginates_and_filters(db_session: Session):
    _, admin, _, users = _clients(db_session)
    user = users["administrator"]
    jobs = [
        _add(db_session, user, 1, status="queued"),
        _add(db_session, user, 2, status="running"),
        _add(db_session, user, 3, status="succeeded"),
        _add(db_session, user, 4, status="failed", request=_ga_request(TERM_B, block=True)),
        _add(db_session, user, 5, status="timed_out", request=_ga_request(TERM_B)),
    ]
    newest_first = [str(job.job_id) for job in reversed(jobs)]

    page = admin.get("/api/v1/jobs?limit=2&offset=2").json()
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 2)
    assert [item["job_id"] for item in page["items"]] == newest_first[2:4]

    for status, job in zip(("queued", "running", "succeeded", "failed", "timed_out"), jobs):
        assert _ids(admin.get(f"/api/v1/jobs?status={status}")) == [str(job.job_id)]
    assert admin.get("/api/v1/jobs?status=cancelled").status_code == 422

    assert _ids(admin.get(f"/api/v1/jobs?term_id={TERM_B}")) == newest_first[:2]
    assert _ids(admin.get("/api/v1/jobs?q=timed_out")) == [str(jobs[4].job_id)]
    assert _ids(admin.get(f"/api/v1/jobs?q={str(jobs[0].job_id)[:8]}")) == [str(jobs[0].job_id)]
    assert admin.get("/api/v1/jobs?limit=0").status_code == 422
    assert admin.get("/api/v1/jobs?limit=101").status_code == 422

    block_row = admin.get(f"/api/v1/jobs?q={jobs[3].job_id}").json()["items"][0]
    assert block_row["scope"] == {
        "term_id": str(TERM_B), "academic_year": "2025-2026", "term_number": 1, "block_id": str(BLOCK),
    }
    assert block_row["error"] == {"code": "ga_infeasible", "message": "No feasible schedule."}
    assert block_row["guidance"] == {"mode": "unguided", "rule_set_id": None, "rule_set_version": None}
    assert "result" not in block_row


def test_detail_exposes_config_snapshot_but_not_raw_inputs(db_session: Session):
    committee, _, _, users = _clients(db_session)
    job = _add(db_session, users["scheduler"], 1, status="succeeded")

    body = committee.get(f"/api/v1/jobs/{job.job_id}").json()
    assert body["status"] == "succeeded"
    assert body["config"] == {**CONFIG, "random_seed": "42"}
    assert body["scope"]["term_id"] == str(TERM_A)
    assert body["result"]["metrics"]["random_seed"] == 42
    assert "Sensitive Name" not in str(body)
    assert {"request", "problem", "claim_token"}.isdisjoint(body)


def test_history_rejects_unstorable_query_values(db_session: Session):
    _, admin, _, _ = _clients(db_session)
    assert admin.get("/api/v1/jobs", params={"q": "a\x00b"}).status_code == 422
    assert admin.get("/api/v1/jobs?offset=9223372036854775808").status_code == 422


def test_history_search_matches_literally(db_session: Session):
    _, admin, _, users = _clients(db_session)
    user = users["administrator"]
    exact = _add(db_session, user, 1, status="failed")
    lookalike = _add(db_session, user, 2, status="failed")
    lookalike.error_code = "gaxinfeasible"
    db_session.flush()

    assert _ids(admin.get("/api/v1/jobs?q=ga_infeasible")) == [str(exact.job_id)]
    assert _ids(admin.get("/api/v1/jobs?q=%25")) == []
    assert _ids(admin.get("/api/v1/jobs?q=%20%20")) == [str(lookalike.job_id), str(exact.job_id)]


def _guided_snapshot(rule_set_id: UUID) -> dict:
    from decimal import Decimal

    from app.services.arm.preprocessing import PatternStream
    from app.services.arm.providers import GuidanceRule, HistoricalGuidance
    from app.services.jobs.codecs import encode_guidance_snapshot

    rule = GuidanceRule(
        stream=PatternStream.TEACHER_SUBJECT, faculty_id=uuid4(), subject_code="CS101",
        room_code=None, meeting_days=None, time_slot=None, guidance_score=Decimal("0.5"),
        support=Decimal("0.2"), confidence=Decimal("0.7"), lift=Decimal("1.2"), term_recurrence=2,
    )
    return encode_guidance_snapshot(HistoricalGuidance((rule,), rule_set_id=rule_set_id, version=3))


def test_evaluation_jobs_report_scope_guidance_and_config(db_session: Session):
    _, admin, _, users = _clients(db_session)
    rule_set_id = uuid4()
    request = {
        "version": 7,
        "kind": "evaluation",
        "problem": {"term": _term(TERM_B), "faculty": [{"full_name": "Sensitive Name"}]},
        "historical_guidance": _guided_snapshot(rule_set_id),
        "config": CONFIG,
        "seeds": [1, 2, 3],
        "input_fingerprint": "f" * 64,
    }
    job = _add(db_session, users["administrator"], 1, kind="evaluation", request=request)

    rows = admin.get(f"/api/v1/jobs?term_id={TERM_B}").json()["items"]
    assert [item["job_id"] for item in rows] == [str(job.job_id)]
    assert rows[0]["scope"]["term_id"] == str(TERM_B)
    assert rows[0]["guidance"] == {
        "mode": "guided", "rule_set_id": str(rule_set_id), "rule_set_version": 3,
    }

    detail = admin.get(f"/api/v1/jobs/{job.job_id}").json()
    assert detail["config"] == {**CONFIG, "random_seed": "42", "seeds": ["1", "2", "3"]}
    assert "Sensitive Name" not in str(detail) and "CS101" not in str(detail)


def test_detail_seeds_survive_beyond_javascript_precision(db_session: Session):
    _, admin, _, users = _clients(db_session)
    big = 2**63 - 1
    request = {
        "version": 6, "problem": {"term": _term(TERM_A)},
        "config": {**CONFIG, "random_seed": big}, "seeds": [big, 7],
    }
    job = _add(db_session, users["administrator"], 1, kind="evaluation", request=request)

    config = admin.get(f"/api/v1/jobs/{job.job_id}").json()["config"]
    assert config["random_seed"] == "9223372036854775807"
    assert config["seeds"] == ["9223372036854775807", "7"]


def test_legacy_semester_is_read_as_term_number(db_session: Session):
    """Codec v1-3 payloads stored the term number under ``semester``."""
    _, admin, _, users = _clients(db_session)
    term = {"term_id": str(TERM_A), "academic_year": "2023-2024", "semester": 2, "is_active": False}
    job = _add(db_session, users["administrator"], 1, request={"version": 3, "problem": {"term": term}, "config": CONFIG})

    expected = {"term_id": str(TERM_A), "academic_year": "2023-2024", "term_number": 2, "block_id": None}
    assert admin.get(f"/api/v1/jobs?term_id={TERM_A}").json()["items"][0]["scope"] == expected
    assert admin.get(f"/api/v1/jobs/{job.job_id}").json()["scope"] == expected


def test_malformed_payload_does_not_break_history(db_session: Session):
    _, admin, _, users = _clients(db_session)
    user = users["administrator"]
    bad = _add(db_session, user, 1, request={"version": 7, "problem": "x", "block_context": ["y"], "config": 3})
    worse = _add(db_session, user, 2, request={"version": 7, "block_context": {"context": "z"}})
    good = _add(db_session, user, 3)

    assert _ids(admin.get("/api/v1/jobs")) == [str(good.job_id), str(worse.job_id), str(bad.job_id)]
    for job in (bad, worse):
        detail = admin.get(f"/api/v1/jobs/{job.job_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["scope"] is None
        assert detail.json()["config"] is None


def test_delete_removes_finished_jobs_and_reports_the_rest(db_session: Session):
    from app.models import AuditEvent, Schedule, Term

    committee, admin, _, users = _clients(db_session)
    scheduler = users["scheduler"]
    done = _add(db_session, scheduler, 1, status="succeeded")
    failed = _add(db_session, scheduler, 2, status="failed")
    running = _add(db_session, scheduler, 3, status="running")
    queued = _add(db_session, scheduler, 4, status="queued")
    published = _add(db_session, scheduler, 5, status="succeeded")
    others = _add(db_session, users["other-scheduler"], 6, status="failed")
    term = Term(academic_year="2025-2026", term_number=1, is_active=False)
    db_session.add(term)
    db_session.flush()
    db_session.add(Schedule(term_id=term.term_id, job_id=published.job_id))
    db_session.flush()
    missing = uuid4()

    ids = [done, failed, running, queued, published, others]
    response = committee.post(
        "/api/v1/jobs/delete",
        json={"job_ids": [str(job.job_id) for job in ids] + [str(missing)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["deleted"]) == {str(done.job_id), str(failed.job_id)}
    assert {item["job_id"]: item["reason"] for item in body["skipped"]} == {
        str(running.job_id): "active",
        str(queued.job_id): "active",
        str(published.job_id): "in_use",
        # Another user's job is invisible to a committee member, like read_job.
        str(others.job_id): "not_found",
        str(missing): "not_found",
    }
    db_session.expire_all()
    assert db_session.get(ExecutionJob, done.job_id) is None
    assert db_session.get(ExecutionJob, running.job_id) is not None
    event = db_session.scalars(select(AuditEvent).where(AuditEvent.operation == "job.delete")).one()
    assert event.details["count"] == 2

    # An administrator may delete anyone's finished job.
    response = admin.post("/api/v1/jobs/delete", json={"job_ids": [str(others.job_id)]})
    assert response.json()["deleted"] == [str(others.job_id)]
    assert committee.post("/api/v1/jobs/delete", json={"job_ids": []}).status_code == 422


def test_committee_member_cannot_delete_arm_jobs(db_session: Session):
    committee, _, _, users = _clients(db_session)
    arm = _add(db_session, users["scheduler"], 1, kind="arm", status="failed", request={"terms": []})

    body = committee.post("/api/v1/jobs/delete", json={"job_ids": [str(arm.job_id)]}).json()

    assert body == {"deleted": [], "skipped": [{"job_id": str(arm.job_id), "reason": "not_found"}]}


def test_display_id_reads_term_block_rule_set_and_run_number(db_session: Session):
    _, admin, _, users = _clients(db_session)
    user = users["administrator"]
    rule_set_id = uuid4()

    def block_request(guided: bool) -> dict:
        request = _ga_request(TERM_A, block=True)
        request["block_context"]["context"]["selected_block"] = {
            "program_code": "BSCS", "year_level": 1, "section_letter": "B",
        }
        if guided:
            request["version"] = 7
            request["historical_guidance"] = _guided_snapshot(rule_set_id)
        return request

    first = _add(db_session, user, 1, request=block_request(True))
    second = _add(db_session, user, 2, request=block_request(True))
    unguided = _add(db_session, user, 3, request=block_request(False))
    term_wide = _add(db_session, user, 4)
    arm = _add(db_session, user, 5, kind="arm", request={"terms": ["raw"]})

    rows = {item["job_id"]: item["display_id"] for item in admin.get("/api/v1/jobs").json()["items"]}
    assert rows[str(first.job_id)] == "2501CS1BV301"
    assert rows[str(second.job_id)] == "2501CS1BV302"
    assert rows[str(unguided.job_id)] == "2501CS1BV001"
    assert rows[str(term_wide.job_id)] == "2501ALLV001"
    assert rows[str(arm.job_id)] is None
    # Paging and the detail view number the run the same way.
    assert admin.get("/api/v1/jobs?limit=1&offset=3").json()["items"][0]["display_id"] == "2501CS1BV302"
    assert admin.get(f"/api/v1/jobs/{second.job_id}").json()["display_id"] == "2501CS1BV302"
