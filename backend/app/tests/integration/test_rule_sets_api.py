"""CHR-RBAC-004 / CHR-RBAC-008 admin-only rule-set lifecycle and review API integration tests."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import ExecutionJob, JobType
from app.models.app_user import AppUser
from app.models.extracted_rule import PATTERN_TYPES, ExtractedRule
from app.models.faculty import Faculty
from app.models.historical_snapshot import HistoricalSnapshot
from app.models.room import Room
from app.models.rule_set import RuleSet, RuleSetThreshold
from app.models.subject import Subject
from app.models.term import Term
from app.services import rule_sets as service
from app.services.arm.providers import DatabaseRuleProvider
from app.services.jobs.codecs import encode_guidance_snapshot
from app.tests.support.api import make_api_client, make_client
from app.tests.support.history import seed_historical_terms

pytestmark = pytest.mark.postgres


def _create_term(db: Session, academic_year: str = "2025-2026", term_number: int = 1) -> Term:
    term = Term(
        term_id=uuid4(),
        academic_year=academic_year,
        term_number=term_number,
        is_active=False,
    )
    db.add(term)
    db.flush()
    return term


def _create_subject(db: Session, subject_code: str = "CS101") -> Subject:
    subject = Subject(
        subject_code=subject_code,
        subject_title="Introduction to Computer Science",
        units=3,
        subject_type="LEC",
    )
    db.add(subject)
    db.flush()
    return subject


def _create_faculty(db: Session, name: str = "Dr. Smith") -> Faculty:
    faculty = Faculty(
        faculty_id=uuid4(),
        full_name=name,
        is_full_time=True,
        availability_mask="1" * 255,
    )
    db.add(faculty)
    db.flush()
    return faculty


def _create_rule_set(
    db: Session,
    version: int = 1,
    status: str = "draft",
    notes: str | None = None,
) -> RuleSet:
    rule_set = RuleSet(
        rule_set_id=uuid4(),
        version=version,
        status=status,
        notes=notes,
    )
    db.add(rule_set)
    db.flush()
    return rule_set


def _create_threshold(
    db: Session,
    rule_set: RuleSet,
    pattern_type: str = "teacher_subject",
) -> RuleSetThreshold:
    threshold = RuleSetThreshold(
        threshold_id=uuid4(),
        rule_set_id=rule_set.rule_set_id,
        pattern_type=pattern_type,
        min_support=Decimal("0.1000"),
        min_confidence=Decimal("0.2000"),
        min_lift=Decimal("0.0000"),
        min_term_recurrence=1,
        earliest_academic_year="2020-2021",
    )
    db.add(threshold)
    db.flush()
    return threshold


def _create_rule(
    db: Session,
    rule_set: RuleSet,
    faculty: Faculty,
    pattern_type: str = "teacher_subject",
    subject_code: str | None = "CS101",
    room_code: str | None = None,
    meeting_days: str | None = None,
    time_slot: str | None = None,
) -> ExtractedRule:
    rule = ExtractedRule(
        rule_id=uuid4(),
        rule_set_id=rule_set.rule_set_id,
        pattern_type=pattern_type,
        faculty_id=faculty.faculty_id,
        subject_code=subject_code,
        room_code=room_code,
        meeting_days=meeting_days,
        time_slot=time_slot,
        support=Decimal("0.5000"),
        confidence=Decimal("0.7500"),
        lift=Decimal("1.2000"),
        term_recurrence=3,
        guidance_score=Decimal("0.8000"),
    )
    db.add(rule)
    db.flush()
    return rule


def _create_room(db: Session, room_code: str = "LB101") -> Room:
    room = Room(
        room_code=room_code,
        building_name="Bunzel",
        room_type="LEC",
        capacity=40,
    )
    db.add(room)
    db.flush()
    return room


def _create_valid_complete_rule_set(
    db: Session,
    version: int = 1,
    status: str = "draft",
    notes: str | None = None,
) -> RuleSet:
    faculty = _create_faculty(db, name=f"Faculty {version}")
    subject = _create_subject(db, subject_code=f"CS{version:03d}")
    room = _create_room(db, room_code=f"RM{version:03d}")
    term = _create_term(db, academic_year="2025-2026", term_number=version)

    rs = _create_rule_set(db, version=version, status=status, notes=notes)
    rs.included_terms.append(term)
    for stream in ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"):
        _create_threshold(db, rs, pattern_type=stream)
    _create_rule(db, rs, faculty, "teacher_subject", subject_code=subject.subject_code)
    _create_rule(db, rs, faculty, "teacher_day", subject_code=None, meeting_days="MWF")
    _create_rule(db, rs, faculty, "teacher_time", subject_code=None, time_slot="07:30-08:30")
    _create_rule(
        db,
        rs,
        faculty,
        "teacher_subject_room",
        subject_code=subject.subject_code,
        room_code=room.room_code,
    )
    db.flush()
    return rs


def test_list_rule_sets_returns_version_ordered_results(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")

    rs2 = _create_rule_set(db_session, version=2, notes="second")
    rs1 = _create_rule_set(db_session, version=1, notes="first")
    _create_threshold(db_session, rs1, "teacher_subject")
    _create_threshold(db_session, rs2, "teacher_subject")

    listed = client.get("/api/v1/rule-sets")

    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    assert items[0]["version"] == 2
    assert items[1]["version"] == 1


def test_list_rule_sets_includes_counts(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    faculty = _create_faculty(db_session)
    subject = _create_subject(db_session)

    rs = _create_rule_set(db_session, version=1)
    # Three terms against one threshold and two rules, so a count read from the
    # wrong table cannot coincidentally match the expected value.
    rs.included_terms.append(_create_term(db_session, term_number=1))
    rs.included_terms.append(_create_term(db_session, term_number=2))
    rs.included_terms.append(_create_term(db_session, term_number=3))
    _create_threshold(db_session, rs, "teacher_subject")
    _create_rule(db_session, rs, faculty, "teacher_subject")
    _create_rule(db_session, rs, faculty, "teacher_day", subject_code=None, meeting_days="MWF")
    db_session.commit()

    listed = client.get("/api/v1/rule-sets")
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["term_count"] == 3
    assert items[0]["rule_count"] == 2


def test_read_rule_set_detail_returns_full_info(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    faculty = _create_faculty(db_session)
    subject = _create_subject(db_session)
    term = _create_term(db_session)

    rs = _create_rule_set(db_session, version=1, notes="test notes")
    rs.included_terms.append(term)
    _create_threshold(db_session, rs, "teacher_subject")
    _create_threshold(db_session, rs, "teacher_day")
    _create_rule(db_session, rs, faculty, "teacher_subject")
    _create_rule(db_session, rs, faculty, "teacher_day", subject_code=None, meeting_days="MWF")
    _create_rule(db_session, rs, faculty, "teacher_day", subject_code=None, meeting_days="TTh")
    db_session.commit()

    detail = client.get(f"/api/v1/rule-sets/{rs.rule_set_id}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["notes"] == "test notes"
    assert len(body["included_terms"]) == 1
    assert body["included_terms"][0]["academic_year"] == "2025-2026"
    assert len(body["thresholds"]) == 2
    assert len(body["streams"]) == 2

    subject_stream = next(s for s in body["streams"] if s["pattern_type"] == "teacher_subject")
    assert subject_stream["rule_count"] == 1
    day_stream = next(s for s in body["streams"] if s["pattern_type"] == "teacher_day")
    assert day_stream["rule_count"] == 2
    # Published before mining provenance was recorded.
    assert (body["source_job_id"], body["requested_by_user_id"]) == (None, None)
    assert body["requested_by_username"] is None
    assert body["job_id"] is None
    assert (body["source_snapshots"], body["mining_summary"]) == ([], None)


def test_read_rule_set_detail_returns_mining_provenance(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    term = _create_term(db_session)
    snapshot = HistoricalSnapshot(
        source_term_id=term.term_id, academic_year="2025-2026", term_number=1
    )
    db_session.add(snapshot)
    job_id, requester = uuid4(), uuid4()
    rs = _create_rule_set(db_session, version=1)
    rs.source_job_id, rs.requested_by_user_id = job_id, requester
    rs.mining_summary = {"persisted_rule_count": 1, "streams": []}
    rs.source_snapshots.append(snapshot)
    db_session.commit()
    # Renaming the operational term must not change the recorded source period.
    term.academic_year = "2030-2031"
    db_session.commit()

    body = client.get(f"/api/v1/rule-sets/{rs.rule_set_id}").json()

    assert body["source_job_id"] == str(job_id)
    assert body["requested_by_user_id"] == str(requester)
    # The requester's account no longer exists, so there is no name to show.
    assert body["requested_by_username"] is None
    assert body["source_snapshots"] == [
        {
            "snapshot_id": str(snapshot.snapshot_id),
            "academic_year": "2025-2026",
            "term_number": 1,
        }
    ]
    assert body["mining_summary"] == {"persisted_rule_count": 1, "streams": []}


def test_read_rule_set_detail_returns_the_publishing_job_of_a_pre_provenance_version(
    db_session: Session,
):
    client = make_api_client(db_session, role="department_administrator")
    user = db_session.scalar(select(AppUser))
    job = ExecutionJob(
        job_type=JobType.ARM,
        requested_by=user.user_id,
        request={},
        timeout_seconds=60,
        max_attempts=1,
    )
    db_session.add(job)
    db_session.flush()
    rs = _create_rule_set(db_session, version=1)
    # Mined before CHR-129: the publishing job is linked, provenance is not.
    rs.job_id = job.job_id
    db_session.commit()

    body = client.get(f"/api/v1/rule-sets/{rs.rule_set_id}").json()

    assert body["job_id"] == str(job.job_id)
    assert (body["source_job_id"], body["mining_summary"]) == (None, None)


def test_read_rule_set_detail_names_the_requesting_administrator(db_session: Session):
    client = make_api_client(db_session, role="department_administrator", username="registrar")
    requester = db_session.scalar(select(AppUser.user_id).where(AppUser.username == "registrar"))
    rs = _create_rule_set(db_session, version=1)
    rs.requested_by_user_id = requester
    db_session.commit()

    body = client.get(f"/api/v1/rule-sets/{rs.rule_set_id}").json()

    assert body["requested_by_username"] == "registrar"


def test_read_rule_set_returns_404_for_missing_id(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    missing_id = uuid4()

    resp = client.get(f"/api/v1/rule-sets/{missing_id}")

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_rule_set_routes_require_admin_role(db_session: Session):
    client = make_api_client(db_session, role="scheduling_committee")

    assert client.get("/api/v1/rule-sets").status_code == 403


def test_rule_set_routes_require_authentication(db_session: Session):
    client = make_client(db_session)

    assert client.get("/api/v1/rule-sets").status_code == 401


def test_list_rule_sets_with_empty_database(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")

    listed = client.get("/api/v1/rule-sets")

    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_read_rule_set_detail_orders_nested_collections(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    faculty = _create_faculty(db_session)
    _create_subject(db_session)

    rs = _create_rule_set(db_session, version=1)
    # Inserted out of order so a response that echoes insertion order fails.
    rs.included_terms.append(_create_term(db_session, "2025-2026", term_number=2))
    rs.included_terms.append(_create_term(db_session, "2024-2025", term_number=1))
    rs.included_terms.append(_create_term(db_session, "2025-2026", term_number=1))
    _create_threshold(db_session, rs, "teacher_subject")
    _create_threshold(db_session, rs, "teacher_day")
    _create_rule(db_session, rs, faculty, "teacher_subject")
    db_session.commit()

    body = client.get(f"/api/v1/rule-sets/{rs.rule_set_id}").json()

    assert [
        (term["academic_year"], term["term_number"]) for term in body["included_terms"]
    ] == [("2024-2025", 1), ("2025-2026", 1), ("2025-2026", 2)]
    assert [threshold["pattern_type"] for threshold in body["thresholds"]] == [
        "teacher_day",
        "teacher_subject",
    ]


def test_activate_draft_rule_set_succeeds_and_sets_active(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_valid_complete_rule_set(db_session, version=1, status="draft", notes="candidate")
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs.rule_set_id}/activate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["activated_at"] is not None
    assert body["notes"] == "candidate"

    db_session.expire_all()
    reloaded = db_session.get(RuleSet, rs.rule_set_id)
    assert reloaded.status == "active"
    assert reloaded.activated_at is not None


def test_activate_supersedes_incumbent_atomically_leaving_exactly_one_active(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs1 = _create_valid_complete_rule_set(db_session, version=1, status="active")
    rs2 = _create_valid_complete_rule_set(db_session, version=2, status="draft")
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs2.rule_set_id}/activate")

    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    db_session.expire_all()
    active_sets = db_session.scalars(select(RuleSet).where(RuleSet.status == "active")).all()
    assert len(active_sets) == 1
    assert active_sets[0].rule_set_id == rs2.rule_set_id

    reloaded_rs1 = db_session.get(RuleSet, rs1.rule_set_id)
    assert reloaded_rs1.status == "superseded"


def test_rollback_superseded_rule_set_succeeds_and_supersedes_incumbent(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs1 = _create_valid_complete_rule_set(db_session, version=1, status="superseded", notes="good v1")
    rs2 = _create_valid_complete_rule_set(db_session, version=2, status="active", notes="bad v2")
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs1.rule_set_id}/rollback")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["activated_at"] is not None
    assert body["notes"] == "good v1"

    db_session.expire_all()
    active_sets = db_session.scalars(select(RuleSet).where(RuleSet.status == "active")).all()
    assert len(active_sets) == 1
    assert active_sets[0].rule_set_id == rs1.rule_set_id

    reloaded_rs2 = db_session.get(RuleSet, rs2.rule_set_id)
    assert reloaded_rs2.status == "superseded"


@pytest.mark.parametrize(
    ("status", "action"), [("draft", "activate"), ("superseded", "rollback")]
)
@pytest.mark.parametrize(
    "bad_rules",
    [
        [{"pattern_type": "teacher_time", "time_slot": slot} for slot in ("07:30-09:00", "LEC 07:30-09:00")],
        [{"pattern_type": "teacher_day", "meeting_days": "   "}],
        [{"pattern_type": "teacher_time", "time_slot": "   "}],
    ],
    ids=["normalized-collision", "blank-meeting-days", "blank-time-slot"],
)
def test_transition_refuses_guidance_the_ga_cannot_load_and_keeps_incumbent(
    db_session: Session, status: str, action: str, bad_rules: list[dict]
):
    client = make_api_client(db_session, role="department_administrator")
    incumbent = _create_valid_complete_rule_set(db_session, version=1, status="active")
    target = _create_valid_complete_rule_set(db_session, version=2, status=status)
    faculty = _create_faculty(db_session, name="Unloadable guidance faculty")
    for rule in bad_rules:
        _create_rule(db_session, target, faculty, subject_code=None, **rule)
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{target.rule_set_id}/{action}")

    assert resp.status_code == 409
    assert resp.json()["code"] == "RULE_SET_INVALID"
    db_session.expire_all()
    assert db_session.get(RuleSet, incumbent.rule_set_id).status == "active"
    assert db_session.get(RuleSet, target.rule_set_id).status == status
    guidance = DatabaseRuleProvider(db_session).get_rules()
    assert guidance.rule_set_id == incumbent.rule_set_id
    encode_guidance_snapshot(guidance)


def test_activate_and_rollback_require_admin_role(db_session: Session):
    client = make_api_client(db_session, role="scheduling_committee")
    rs1 = _create_valid_complete_rule_set(db_session, version=1, status="draft")
    rs2 = _create_valid_complete_rule_set(db_session, version=2, status="superseded")
    db_session.commit()

    assert client.post(f"/api/v1/rule-sets/{rs1.rule_set_id}/activate").status_code == 403
    assert client.post(f"/api/v1/rule-sets/{rs2.rule_set_id}/rollback").status_code == 403


def test_activate_and_rollback_require_authentication(db_session: Session):
    client = make_client(db_session)
    rs1 = _create_valid_complete_rule_set(db_session, version=1, status="draft")
    rs2 = _create_valid_complete_rule_set(db_session, version=2, status="superseded")
    db_session.commit()

    assert client.post(f"/api/v1/rule-sets/{rs1.rule_set_id}/activate").status_code == 401
    assert client.post(f"/api/v1/rule-sets/{rs2.rule_set_id}/rollback").status_code == 401


def test_activate_and_rollback_return_404_for_missing_id(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    missing_id = uuid4()

    assert client.post(f"/api/v1/rule-sets/{missing_id}/activate").status_code == 404
    assert client.post(f"/api/v1/rule-sets/{missing_id}/rollback").status_code == 404


def test_activate_and_rollback_status_conflicts_return_409(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs_draft = _create_valid_complete_rule_set(db_session, version=1, status="draft")
    rs_active = _create_valid_complete_rule_set(db_session, version=2, status="active")
    rs_superseded = _create_valid_complete_rule_set(db_session, version=3, status="superseded")
    db_session.commit()

    # Activating active or superseded
    resp1 = client.post(f"/api/v1/rule-sets/{rs_active.rule_set_id}/activate")
    assert resp1.status_code == 409
    assert resp1.json()["code"] == "RULE_SET_STATUS_CONFLICT"
    resp2 = client.post(f"/api/v1/rule-sets/{rs_superseded.rule_set_id}/activate")
    assert resp2.status_code == 409
    assert resp2.json()["code"] == "RULE_SET_STATUS_CONFLICT"

    # Rolling back active or draft
    resp3 = client.post(f"/api/v1/rule-sets/{rs_active.rule_set_id}/rollback")
    assert resp3.status_code == 409
    assert resp3.json()["code"] == "RULE_SET_STATUS_CONFLICT"
    resp4 = client.post(f"/api/v1/rule-sets/{rs_draft.rule_set_id}/rollback")
    assert resp4.status_code == 409
    assert resp4.json()["code"] == "RULE_SET_STATUS_CONFLICT"


def test_activate_fails_validation_if_no_included_terms_returns_409(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_rule_set(db_session, version=1, status="draft")
    for stream in ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"):
        _create_threshold(db_session, rs, pattern_type=stream)
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs.rule_set_id}/activate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RULE_SET_INVALID"
    assert "term" in resp.json()["message"].lower()

    db_session.expire_all()
    assert db_session.get(RuleSet, rs.rule_set_id).status == "draft"


def test_activate_fails_validation_if_missing_threshold_stream_returns_409(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_rule_set(db_session, version=1, status="draft")
    rs.included_terms.append(_create_term(db_session))
    # Only create 3 streams instead of 4
    _create_threshold(db_session, rs, pattern_type="teacher_subject")
    _create_threshold(db_session, rs, pattern_type="teacher_day")
    _create_threshold(db_session, rs, pattern_type="teacher_time")
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs.rule_set_id}/activate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RULE_SET_INVALID"
    assert "threshold" in resp.json()["message"].lower()


def test_activate_fails_validation_if_threshold_earliest_academic_year_missing_returns_409(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_rule_set(db_session, version=1, status="draft")
    rs.included_terms.append(_create_term(db_session))
    for stream in ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"):
        _create_threshold(db_session, rs, pattern_type=stream)
    target_thresh = [t for t in rs.thresholds if t.pattern_type == "teacher_subject"][0]
    target_thresh.earliest_academic_year = "   "
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs.rule_set_id}/activate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RULE_SET_INVALID"
    assert "earliest_academic_year" in resp.json()["message"].lower()

    db_session.expire_all()
    assert db_session.get(RuleSet, rs.rule_set_id).status == "draft"


def test_activate_fails_validation_if_empty_extracted_rules_returns_409(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_rule_set(db_session, version=1, status="draft")
    rs.included_terms.append(_create_term(db_session))
    for stream in ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"):
        _create_threshold(db_session, rs, pattern_type=stream)
    db_session.commit()

    resp = client.post(f"/api/v1/rule-sets/{rs.rule_set_id}/activate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RULE_SET_INVALID"
    assert "rule" in resp.json()["message"].lower()

    db_session.expire_all()
    assert db_session.get(RuleSet, rs.rule_set_id).status == "draft"


def test_database_rule_provider_serves_active_and_updates_on_rollback(db_session: Session):
    provider = DatabaseRuleProvider(db_session)
    rs1 = _create_valid_complete_rule_set(db_session, version=1, status="draft")
    db_session.commit()

    # Before activation, provider cannot find active rule set
    with pytest.raises(Exception):
        provider.get_rules()

    # Activate rs1
    client = make_api_client(db_session, role="department_administrator")
    resp1 = client.post(f"/api/v1/rule-sets/{rs1.rule_set_id}/activate")
    assert resp1.status_code == 200

    guidance = provider.get_rules()
    assert guidance.version == 1
    assert guidance.rule_set_id == rs1.rule_set_id
    assert len(guidance.rules) == 4

    # Activate rs2 (supersedes rs1)
    rs2 = _create_valid_complete_rule_set(db_session, version=2, status="draft")
    db_session.commit()
    resp2 = client.post(f"/api/v1/rule-sets/{rs2.rule_set_id}/activate")
    assert resp2.status_code == 200

    guidance2 = provider.get_rules()
    assert guidance2.version == 2
    assert guidance2.rule_set_id == rs2.rule_set_id

    # Rollback to rs1
    resp_rb = client.post(f"/api/v1/rule-sets/{rs1.rule_set_id}/rollback")
    assert resp_rb.status_code == 200
    guidance_rollback = provider.get_rules()
    assert guidance_rollback.version == 1
    assert guidance_rollback.rule_set_id == rs1.rule_set_id


def test_concurrent_activation_contenders_yield_one_active_version(migrated_engine):
    """Two concurrent contenders attempting to activate rule sets yield one active version.

    Under SHARE ROW EXCLUSIVE table locking, the transactions are serialized.
    Both contenders commit cleanly without integrity errors or partial writes:
    the first contender activates, and the second supersedes it, leaving exactly
    one active version in the database.
    """
    import threading

    with Session(migrated_engine) as session, session.begin():
        rs1 = _create_valid_complete_rule_set(session, version=101, status="draft")
        rs2 = _create_valid_complete_rule_set(session, version=102, status="draft")
        session.flush()
        id1 = rs1.rule_set_id
        id2 = rs2.rule_set_id

    results = []
    errors = []

    def activate_target(target_id):
        with Session(migrated_engine) as s:
            try:
                res = service.activate_rule_set(s, target_id)
                results.append((target_id, res))
            except Exception as e:
                errors.append((target_id, e))

    try:
        t1 = threading.Thread(target=activate_target, args=(id1,))
        t2 = threading.Thread(target=activate_target, args=(id2,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both contenders serialize cleanly under SHARE ROW EXCLUSIVE table lock
        assert len(errors) == 0
        assert len(results) == 2

        # Check database: exactly one active rule set, and the other is superseded
        with Session(migrated_engine) as session:
            active_sets = session.scalars(select(RuleSet).where(RuleSet.status == "active")).all()
            assert len(active_sets) == 1
            active_id = active_sets[0].rule_set_id
            assert active_id in (id1, id2)

            superseded_id = id2 if active_id == id1 else id1
            superseded_set = session.get(RuleSet, superseded_id)
            assert superseded_set is not None
            assert superseded_set.status == "superseded"
    finally:
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                text("DELETE FROM rule_set WHERE rule_set_id IN (:id1, :id2)"),
                {"id1": id1, "id2": id2},
            )
            session.execute(
                text("DELETE FROM subject WHERE subject_code IN ('CS101', 'CS102')")
            )
            session.execute(
                text("DELETE FROM room WHERE room_code IN ('RM101', 'RM102')")
            )
            session.execute(
                text("DELETE FROM term WHERE term_number IN (101, 102)")
            )
            session.execute(
                text("DELETE FROM faculty WHERE full_name IN ('Faculty 101', 'Faculty 102')")
            )



def test_list_historical_terms_returns_captured_terms_newest_first(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    seed_historical_terms(db_session)
    db_session.commit()

    listed = client.get("/api/v1/rule-sets/historical-terms")

    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [(item["academic_year"], item["term_number"]) for item in items] == [
        ("2024-2025", 2),
        ("2024-2025", 1),
        ("2023-2024", 2),
        ("2023-2024", 1),
    ]
    assert all(item["record_count"] == 1 for item in items)
    assert all(
        item["eligible_streams"] == list(PATTERN_TYPES) for item in items
    )


def test_list_historical_terms_reports_streams_the_year_is_too_old_for(
    db_session: Session,
):
    """A 2018 term may be mined for teacher/subject only; the other three
    streams start at 2023, and the administrator has to see that before
    selecting it."""
    client = make_api_client(db_session, role="department_administrator")
    seed_historical_terms(db_session, periods=(("2018-2019", 1),))
    db_session.commit()

    items = client.get("/api/v1/rule-sets/historical-terms").json()["items"]

    assert [item["eligible_streams"] for item in items] == [["teacher_subject"]]


def test_list_historical_terms_is_not_shadowed_by_the_rule_set_detail_route(
    db_session: Session,
):
    """The literal path must win over /rule-sets/{rule_set_id}, which would
    reject "historical-terms" as a malformed UUID."""
    client = make_api_client(db_session, role="department_administrator")

    listed = client.get("/api/v1/rule-sets/historical-terms")

    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_list_historical_terms_requires_admin_role(db_session: Session):
    client = make_api_client(db_session, role="scheduling_committee")

    assert client.get("/api/v1/rule-sets/historical-terms").status_code == 403


def test_list_historical_terms_requires_authentication(db_session: Session):
    client = make_client(db_session)

    assert client.get("/api/v1/rule-sets/historical-terms").status_code == 401


def _rules_url(rule_set_id, pattern_type: str = "teacher_day", **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in {"pattern_type": pattern_type, **params}.items())
    return f"/api/v1/rule-sets/{rule_set_id}/rules?{query}"


def _seed_day_rules(db: Session, scores: list[str]) -> RuleSet:
    """One draft set with a teacher_day rule per score, inserted in the given order."""
    faculty = _create_faculty(db)
    rs = _create_rule_set(db, version=1)
    for index, score in enumerate(scores):
        rule = _create_rule(db, rs, faculty, "teacher_day", subject_code=None, meeting_days=f"D{index}")
        rule.guidance_score = Decimal(score)
    db.commit()
    return rs


def test_list_rule_set_rules_returns_items_and_metrics_for_one_stream(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _create_valid_complete_rule_set(db_session)
    db_session.commit()

    room_page = client.get(_rules_url(rs.rule_set_id, "teacher_subject_room"))

    assert room_page.status_code == 200
    body = room_page.json()
    assert body["pattern_type"] == "teacher_subject_room"
    assert body["total"] == 1
    [rule] = body["items"]
    assert rule["antecedent"] == ["Faculty 1", "CS001"]
    assert rule["consequent"] == ["RM001"]
    assert Decimal(rule["support"]) == Decimal("0.5")
    assert Decimal(rule["confidence"]) == Decimal("0.75")
    assert Decimal(rule["lift"]) == Decimal("1.2")
    assert rule["term_recurrence"] == 3
    assert Decimal(rule["guidance_score"]) == Decimal("0.8")

    time_rule = client.get(_rules_url(rs.rule_set_id, "teacher_time")).json()["items"][0]
    assert time_rule["antecedent"] == ["Faculty 1"]
    assert time_rule["consequent"] == ["07:30-08:30"]


def test_list_rule_set_rules_orders_by_guidance_score_and_pages(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.2000", "0.9000", "0.5000"])

    desc = client.get(_rules_url(rs.rule_set_id, limit=2)).json()
    assert desc["total"] == 3
    assert [r["guidance_score"] for r in desc["items"]] == ["0.9000", "0.5000"]

    rest = client.get(_rules_url(rs.rule_set_id, limit=2, offset=2)).json()
    assert [r["guidance_score"] for r in rest["items"]] == ["0.2000"]

    asc = client.get(_rules_url(rs.rule_set_id, order="asc")).json()
    assert [r["guidance_score"] for r in asc["items"]] == ["0.2000", "0.5000", "0.9000"]


def test_list_rule_set_rules_filters_to_one_faculty(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.5000"])
    other = _create_faculty(db_session, name="Dr. Other")
    _create_rule(db_session, rs, other, "teacher_day", subject_code=None, meeting_days="TTh")
    db_session.commit()

    page = client.get(_rules_url(rs.rule_set_id, faculty_id=other.faculty_id)).json()

    assert page["total"] == 1
    assert [rule["consequent"] for rule in page["items"]] == [["TTh"]]
    assert client.get(_rules_url(rs.rule_set_id)).json()["total"] == 2


def test_list_rule_set_rules_breaks_score_ties_deterministically(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.5000"] * 4)

    first = [r["rule_id"] for r in client.get(_rules_url(rs.rule_set_id, limit=2)).json()["items"]]
    second = [r["rule_id"] for r in client.get(_rules_url(rs.rule_set_id, limit=2, offset=2)).json()["items"]]

    assert first == sorted(first) and second == sorted(second)
    assert max(first) < min(second)


def test_list_rule_set_rules_empty_stream_returns_no_items(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.5000"])

    body = client.get(_rules_url(rs.rule_set_id, "teacher_subject")).json()

    assert body["total"] == 0
    assert body["items"] == []


def test_list_rule_set_rules_rejects_unknown_stream_and_bad_paging(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.5000"])

    assert client.get(_rules_url(rs.rule_set_id, "teacher_room")).status_code == 422
    assert client.get(_rules_url(rs.rule_set_id, limit=0)).status_code == 422
    assert client.get(_rules_url(rs.rule_set_id, offset=-1)).status_code == 422


def test_list_rule_set_rules_returns_404_for_missing_set(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")

    resp = client.get(_rules_url(uuid4()))

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_list_rule_set_rules_requires_admin_role(db_session: Session):
    client = make_api_client(db_session, role="scheduling_committee")

    assert client.get(_rules_url(uuid4())).status_code == 403


def test_list_rule_set_rules_requires_authentication(db_session: Session):
    client = make_client(db_session)

    assert client.get(_rules_url(uuid4())).status_code == 401


def test_rule_set_rules_are_read_only(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")
    rs = _seed_day_rules(db_session, ["0.5000"])
    url = f"/api/v1/rule-sets/{rs.rule_set_id}/rules"

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, url).status_code == 405



def _metric_row(rule_set, stream, key, name, *, faculty=None, has_rule, meets, support="0.0100"):
    from app.models.rule_set_faculty_metric import RuleSetFacultyMetric

    return RuleSetFacultyMetric(
        rule_set_id=rule_set.rule_set_id,
        pattern_type=stream,
        source_faculty_key=key,
        faculty_id=faculty.faculty_id if faculty else None,
        faculty_name=name,
        subject_code="CS101" if stream == "teacher_subject" else None,
        room_code=None,
        meeting_days="MWF" if stream == "teacher_day" else None,
        time_slot=None,
        offering_count=5,
        support=Decimal(support),
        confidence=Decimal("0.5000"),
        lift=Decimal("2.0000"),
        term_recurrence=3 if meets[3] else 1,
        meets_support=meets[0],
        meets_confidence=meets[1],
        meets_lift=meets[2],
        meets_term_recurrence=meets[3],
        has_rule=has_rule,
    )


def test_faculty_coverage_returns_recorded_metrics_for_the_stream(db_session: Session):
    covered = _create_faculty(db_session, name="Covered Teacher")
    near = _create_faculty(db_session, name="Near Miss")
    _create_subject(db_session)
    rs = _create_rule_set(db_session)
    _create_rule(db_session, rs, covered, "teacher_subject")
    db_session.add_all(
        [
            _metric_row(rs, "teacher_subject", str(covered.faculty_id), "Covered Teacher",
                        faculty=covered, has_rule=True, meets=(True, True, True, True)),
            _metric_row(rs, "teacher_subject", str(near.faculty_id), "Near Miss",
                        faculty=near, has_rule=False, meets=(True, True, True, False)),
            _metric_row(rs, "teacher_subject", "research-key-1", "Former Teacher",
                        has_rule=False, meets=(False, True, False, False), support="0.0010"),
            _metric_row(rs, "teacher_day", str(near.faculty_id), "Near Miss",
                        faculty=near, has_rule=False, meets=(False, False, False, False)),
        ]
    )
    db_session.flush()
    client = make_api_client(db_session, role="department_administrator")

    resp = client.get(
        f"/api/v1/rule-sets/{rs.rule_set_id}/faculty-coverage",
        params={"pattern_type": "teacher_subject"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics_recorded"] is True
    assert (body["teacher_count"], body["with_rule_count"], body["without_rule_count"]) == (3, 1, 2)
    assert body["version_with_rule_count"] == 1
    # Rules first, then the closest miss, then the furthest.
    assert [item["faculty_name"] for item in body["items"]] == [
        "Covered Teacher",
        "Near Miss",
        "Former Teacher",
    ]
    near_row = body["items"][1]
    assert near_row["meets_term_recurrence"] is False
    assert near_row["meets_support"] is True
    assert near_row["subject_code"] == "CS101"
    former = body["items"][2]
    assert former["faculty_id"] is None
    assert former["on_roster"] is False
    assert former["is_active"] is None


def test_faculty_coverage_falls_back_to_rules_when_no_metrics_were_recorded(db_session: Session):
    covered = _create_faculty(db_session, name="Covered Teacher")
    _create_subject(db_session)
    rs = _create_rule_set(db_session)
    _create_rule(db_session, rs, covered, "teacher_subject")
    client = make_api_client(db_session, role="department_administrator")

    resp = client.get(
        f"/api/v1/rule-sets/{rs.rule_set_id}/faculty-coverage",
        params={"pattern_type": "teacher_subject"},
    )

    body = resp.json()
    assert body["metrics_recorded"] is False
    assert body["teacher_count"] == 1
    item = body["items"][0]
    assert item["faculty_name"] == "Covered Teacher"
    assert item["has_rule"] is True
    assert all(item[key] for key in ("meets_support", "meets_confidence", "meets_lift", "meets_term_recurrence"))


def test_faculty_coverage_returns_404_for_missing_rule_set(db_session: Session):
    client = make_api_client(db_session, role="department_administrator")

    resp = client.get(
        f"/api/v1/rule-sets/{uuid4()}/faculty-coverage",
        params={"pattern_type": "teacher_subject"},
    )

    assert resp.status_code == 404
