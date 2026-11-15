"""CHR-100 / CHR-COLAB-004 offline package import against PostgreSQL."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import import_rule_set as import_rule_set_cli
from app.models import (
    ExtractedRule,
    Faculty,
    Room,
    RuleSet,
    RuleSetFacultyMetric,
    RuleSetThreshold,
    Subject,
    Term,
)
from app.services.arm.importer import RulePackageImportError, import_rule_package
from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream
from app.services.arm.rule_package import (
    CandidateRulePackage,
    RulePackageItem,
    RulePackageMetadata,
    RulePackageRule,
    RulePackageTerm,
    RulePackageThreshold,
    write_rule_package,
)


pytestmark = pytest.mark.postgres
RESEARCH_KEY = UUID("10000000-0000-0000-0000-000000000001")


def _rule(
    stream: PatternStream,
    faculty_id: UUID,
    *,
    subject_code: str = "<CHR100-SUBJ>",
    room_code: str = "<CHR100-ROOM>",
) -> RulePackageRule:
    faculty = RulePackageItem(kind="faculty_id", value=str(faculty_id))
    if stream is PatternStream.TEACHER_SUBJECT:
        antecedent = (faculty,)
        consequent = (RulePackageItem(kind="subject_code", value=subject_code),)
    elif stream is PatternStream.TEACHER_DAY:
        antecedent = (faculty,)
        consequent = (RulePackageItem(kind="meeting_days", value="MWF"),)
    elif stream is PatternStream.TEACHER_TIME:
        antecedent = (faculty,)
        consequent = (RulePackageItem(kind="time_slot", value="08:00-09:30"),)
    else:
        antecedent = (
            faculty,
            RulePackageItem(kind="subject_code", value=subject_code),
        )
        consequent = (RulePackageItem(kind="room_code", value=room_code),)
    return RulePackageRule(
        pattern_type=stream,
        research_faculty_key=RESEARCH_KEY,
        faculty_name="<CHR100 FACULTY>",
        antecedent=antecedent,
        consequent=consequent,
        support=Decimal("0.5000"),
        confidence=Decimal("0.7500"),
        lift=Decimal("1.5000"),
        term_recurrence=2,
        guidance_score=Decimal("0.7500"),
    )


def _package(faculty_id: UUID) -> CandidateRulePackage:
    policy = HistoricalInclusionPolicy()
    return CandidateRulePackage(
        schema_version=1,
        traceability="CHR-100 / CHR-COLAB-004",
        candidate_status="draft",
        metadata=RulePackageMetadata(
            mined_at=datetime(2026, 10, 2, 6, tzinfo=timezone.utc),
            packaged_at=datetime(2026, 10, 2, 7, tzinfo=timezone.utc),
            source_manifest_sha256="1" * 64,
            evaluation_manifest_sha256="2" * 64,
            selected_rules_sha256="3" * 64,
            faculty_crosswalk_sha256="4" * 64,
        ),
        training_terms=(
            RulePackageTerm(academic_year="2023-2024", term_number=1),
            RulePackageTerm(academic_year="2023-2024", term_number=2),
        ),
        thresholds=tuple(
            RulePackageThreshold(
                pattern_type=stream,
                min_support=Decimal("0.2500"),
                min_confidence=Decimal("0.5000"),
                min_lift=Decimal("1.1000"),
                min_term_recurrence=1,
                training_term_count=2,
                earliest_academic_year=policy.earliest_academic_year(stream),
            )
            for stream in PatternStream
        ),
        rules=tuple(_rule(stream, faculty_id) for stream in PatternStream),
    )


def _catalog(db_session) -> tuple[Faculty, RuleSet]:
    faculty = Faculty(
        full_name="<CHR100 FACULTY>",
        is_full_time=False,
        availability_mask="1",
    )
    db_session.add_all(
        [
            faculty,
            Subject(
                subject_code="<CHR100-SUBJ>",
                subject_title="<CHR100 SUBJECT>",
                units=3,
                subject_type="LEC",
            ),
            Room(
                room_code="<CHR100-ROOM>",
                building_name="<CHR100 BUILDING>",
                capacity=20,
                room_type="lecture",
                expanded=False,
            ),
        ]
    )
    for term_number in (1, 2):
        db_session.add(
            Term(
                academic_year="2023-2024",
                term_number=term_number,
                is_active=False,
            )
        )
    db_session.flush()

    highest = db_session.scalar(select(func.max(RuleSet.version))) or 0
    active = RuleSet(version=highest + 1, status="active", notes="incumbent")
    db_session.add(active)
    db_session.flush()
    return faculty, active


def test_import_persists_the_complete_package_as_an_unactivated_draft(db_session):
    faculty, active = _catalog(db_session)
    package = _package(faculty.faculty_id)

    summary = import_rule_package(db_session, package, notes="review this candidate")

    imported = db_session.get(RuleSet, summary.rule_set_id)
    assert imported is not None
    assert imported.status == "draft"
    assert imported.version == active.version + 1
    assert imported.generated_at == package.metadata.mined_at
    assert imported.notes == "review this candidate"
    assert db_session.get(RuleSet, active.rule_set_id).status == "active"
    assert {
        (term.academic_year, term.term_number) for term in imported.included_terms
    } == {("2023-2024", 1), ("2023-2024", 2)}
    assert summary.rule_count == 4
    assert summary.training_term_count == 2
    assert db_session.scalar(
        select(func.count())
        .select_from(RuleSetThreshold)
        .where(RuleSetThreshold.rule_set_id == imported.rule_set_id)
    ) == 4
    assert db_session.scalar(
        select(func.count())
        .select_from(ExtractedRule)
        .where(ExtractedRule.rule_set_id == imported.rule_set_id)
    ) == 4


def test_import_rejects_all_unknown_references_before_writing(db_session):
    faculty, _active = _catalog(db_session)
    payload = _package(faculty.faculty_id).model_dump(mode="json")
    unknown_faculty = str(uuid4())
    for rule in payload["rules"]:
        rule["antecedent"][0]["value"] = unknown_faculty
        if rule["pattern_type"] == "teacher_subject":
            rule["consequent"][0]["value"] = "<UNKNOWN-SUBJ>"
        if rule["pattern_type"] == "teacher_subject_room":
            rule["consequent"][0]["value"] = "<UNKNOWN-ROOM>"
    payload["training_terms"][0]["academic_year"] = "2025-2026"
    package = CandidateRulePackage.model_validate(payload)
    before = db_session.scalar(select(func.count()).select_from(RuleSet))

    with pytest.raises(RulePackageImportError) as caught:
        import_rule_package(db_session, package)

    message = str(caught.value)
    assert "unknown faculty_id" in message
    assert "unknown subject_code" in message
    assert "unknown room_code" in message
    assert "unknown training terms" in message
    assert db_session.scalar(select(func.count()).select_from(RuleSet)) == before


def test_cli_dry_run_rolls_back_before_the_real_import(
    db_session, tmp_path, monkeypatch, capsys
):
    faculty, _active = _catalog(db_session)
    artifact = tmp_path / "candidate_rule_set.json"
    write_rule_package(_package(faculty.faculty_id), artifact)
    connection = db_session.get_bind()
    monkeypatch.setattr(
        import_rule_set_cli,
        "SessionLocal",
        lambda: Session(bind=connection, join_transaction_mode="create_savepoint"),
    )
    before = db_session.scalar(select(func.count()).select_from(RuleSet))

    assert import_rule_set_cli.main(["--artifact", str(artifact), "--dry-run"]) == 0
    assert db_session.scalar(select(func.count()).select_from(RuleSet)) == before
    assert "validated and rolled back draft RuleSet" in capsys.readouterr().out

    assert import_rule_set_cli.main(["--artifact", str(artifact)]) == 0
    assert db_session.scalar(select(func.count()).select_from(RuleSet)) == before + 1
    imported = db_session.scalars(
        select(RuleSet).where(RuleSet.notes.like("Offline import; artifact sha256=%"))
    ).one()
    assert imported.status == "draft"


def test_import_retries_a_rule_set_version_collision(db_session, monkeypatch):
    faculty, active = _catalog(db_session)
    package = _package(faculty.faculty_id)
    original_scalar = db_session.scalar
    rival_versions = []

    def claim_first_version(*args, **kwargs):
        highest = original_scalar(*args, **kwargs)
        if not rival_versions:
            rival_versions.append((highest or 0) + 1)
            db_session.add(
                RuleSet(
                    version=rival_versions[0],
                    status="draft",
                    notes="concurrent rival",
                )
            )
            db_session.flush()
        return highest

    monkeypatch.setattr(db_session, "scalar", claim_first_version)

    summary = import_rule_package(db_session, package)

    assert rival_versions == [active.version + 1]
    assert summary.version == rival_versions[0] + 1
    assert db_session.get(RuleSet, summary.rule_set_id).status == "draft"


def test_import_records_a_version_label_and_refuses_a_duplicate(db_session):
    faculty, _active = _catalog(db_session)
    package = _package(faculty.faculty_id)
    label = f"1.1-{uuid4().hex[:6]}"

    summary = import_rule_package(db_session, package, version_label=label)

    assert summary.version_label == label
    assert db_session.get(RuleSet, summary.rule_set_id).version_label == label
    with pytest.raises(RulePackageImportError, match="already used"):
        import_rule_package(db_session, package, version_label=label)


def _write_metrics_artifact(path, faculty_id: UUID) -> None:
    def metric(stream: PatternStream, key: str, *, linked: bool, passed: bool):
        return {
            "stream": stream.value,
            "source_faculty_key": key,
            "faculty_name": "<CHR100 FACULTY>" if linked else "<NOT PASSED>",
            "faculty_id": str(faculty_id) if linked else None,
            "subject_code": (
                "<CHR100-SUBJ>" if stream is PatternStream.TEACHER_SUBJECT else None
            ),
            "room_code": None,
            "meeting_days": None,
            "time_slot": None,
            "offering_count": 4,
            "support": "0.5000" if passed else "0.0100",
            "confidence": "0.7500",
            "lift": "1.5000",
            "term_recurrence": 2,
            "meets_support": passed,
            "meets_confidence": True,
            "meets_lift": True,
            "meets_term_recurrence": True,
        }

    threshold = {
        "min_support": "0.2500",
        "min_confidence": "0.5000",
        "min_lift": "1.1000",
        "min_term_recurrence": 1,
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_run": "run",
                "input_sha256": {},
                "thresholds": {stream.value: threshold for stream in PatternStream},
                "streams": {
                    stream.value: {"training_term_count": 2, "transaction_count": 10}
                    for stream in PatternStream
                },
                "metrics": [
                    metric(stream, "key-linked", linked=True, passed=True)
                    for stream in PatternStream
                ]
                + [
                    metric(
                        PatternStream.TEACHER_SUBJECT,
                        "key-failed",
                        linked=False,
                        passed=False,
                    )
                ],
            }
        )
    )


def _rule_rows(db_session, rule_set: RuleSet) -> list[tuple]:
    return sorted(
        (
            rule.pattern_type,
            rule.faculty_id,
            rule.subject_code,
            rule.room_code,
            rule.meeting_days,
            rule.time_slot,
            rule.support,
            rule.confidence,
            rule.lift,
            rule.term_recurrence,
            rule.guidance_score,
        )
        for rule in db_session.scalars(
            select(ExtractedRule).where(ExtractedRule.rule_set_id == rule_set.rule_set_id)
        )
    )


def test_cli_republishes_the_same_rules_with_teacher_coverage(
    db_session, tmp_path, monkeypatch, capsys
):
    faculty, _active = _catalog(db_session)
    artifact = tmp_path / "candidate_rule_set.json"
    metrics = tmp_path / "faculty_metrics.json"
    write_rule_package(_package(faculty.faculty_id), artifact)
    _write_metrics_artifact(metrics, faculty.faculty_id)
    connection = db_session.get_bind()
    monkeypatch.setattr(
        import_rule_set_cli,
        "SessionLocal",
        lambda: Session(bind=connection, join_transaction_mode="create_savepoint"),
    )
    label = f"1.1-{uuid4().hex[:6]}"

    assert import_rule_set_cli.main(["--artifact", str(artifact)]) == 0
    args = ["--artifact", str(artifact), "--faculty-metrics", str(metrics)]
    assert import_rule_set_cli.main([*args, "--version-label", label]) == 0
    out = capsys.readouterr().out
    assert f"(label {label})" in out
    assert "5 faculty metric rows" in out

    relabelled = db_session.scalars(
        select(RuleSet).where(RuleSet.version_label == label)
    ).one()
    original = db_session.scalars(
        select(RuleSet).where(RuleSet.version == relabelled.version - 1)
    ).one()
    assert _rule_rows(db_session, relabelled) == _rule_rows(db_session, original)
    assert {t.term_id for t in relabelled.included_terms} == {
        t.term_id for t in original.included_terms
    }
    coverage = {
        row.source_faculty_key: row.has_rule
        for row in db_session.scalars(
            select(RuleSetFacultyMetric).where(
                RuleSetFacultyMetric.rule_set_id == relabelled.rule_set_id
            )
        )
    }
    assert coverage == {"key-linked": True, "key-failed": False}
