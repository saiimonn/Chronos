"""Attaching an offline faculty-metrics package to an imported RuleSet."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RuleSetFacultyMetric
from app.services.arm.faculty_metrics_package import (
    FacultyMetricsPackage,
    FacultyMetricsPackageError,
    attach_faculty_metrics,
)
from app.tests.integration.test_rule_sets_api import (
    _create_faculty,
    _create_rule,
    _create_rule_set,
    _create_subject,
    _create_threshold,
)

pytestmark = pytest.mark.postgres

STREAMS = ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room")
# _create_threshold records these for every stream.
THRESHOLD = {
    "min_support": "0.1000",
    "min_confidence": "0.2000",
    "min_lift": "0.0000",
    "min_term_recurrence": 1,
}


def _metric(stream, key, *, faculty_id=None, passed=True, name="Teacher"):
    return {
        "stream": stream,
        "source_faculty_key": key,
        "faculty_name": name,
        "faculty_id": str(faculty_id) if faculty_id else None,
        "subject_code": "CS101" if stream == "teacher_subject" else None,
        "room_code": None,
        "meeting_days": None,
        "time_slot": None,
        "offering_count": 4,
        "support": "0.2000" if passed else "0.0100",
        "confidence": "0.5000",
        "lift": "1.5000",
        "term_recurrence": 2,
        "meets_support": passed,
        "meets_confidence": True,
        "meets_lift": True,
        "meets_term_recurrence": True,
    }


def _package(metrics, thresholds=None):
    return FacultyMetricsPackage.model_validate(
        {
            "schema_version": 1,
            "source_run": "run",
            "input_sha256": {},
            "thresholds": thresholds or {stream: THRESHOLD for stream in STREAMS},
            "streams": {
                stream: {"training_term_count": 2, "transaction_count": 10}
                for stream in STREAMS
            },
            "metrics": metrics,
        }
    )


@pytest.fixture
def imported(db_session: Session):
    covered = _create_faculty(db_session, name="Covered")
    _create_subject(db_session)
    rule_set = _create_rule_set(db_session)
    for stream in STREAMS:
        _create_threshold(db_session, rule_set, pattern_type=stream)
    _create_rule(db_session, rule_set, covered, "teacher_subject")
    return rule_set, covered


def test_attaches_rows_for_linked_and_unlinked_teachers(db_session, imported):
    rule_set, covered = imported
    package = _package(
        [
            _metric("teacher_subject", "key-covered", faculty_id=covered.faculty_id),
            _metric("teacher_subject", "key-unlinked", passed=False, name="Unlinked"),
        ]
    )

    assert attach_faculty_metrics(db_session, rule_set.rule_set_id, package) == 2

    rows = {
        row.source_faculty_key: row
        for row in db_session.scalars(
            select(RuleSetFacultyMetric).where(
                RuleSetFacultyMetric.rule_set_id == rule_set.rule_set_id
            )
        )
    }
    assert rows["key-covered"].has_rule is True
    assert rows["key-covered"].faculty_id == covered.faculty_id
    assert rows["key-unlinked"].has_rule is False
    assert rows["key-unlinked"].faculty_id is None
    assert rows["key-unlinked"].support == Decimal("0.0100")


def test_refuses_a_second_attach(db_session, imported):
    rule_set, covered = imported
    package = _package(
        [_metric("teacher_subject", "key-covered", faculty_id=covered.faculty_id)]
    )
    attach_faculty_metrics(db_session, rule_set.rule_set_id, package)

    with pytest.raises(FacultyMetricsPackageError, match="already has"):
        attach_faculty_metrics(db_session, rule_set.rule_set_id, package)


def test_refuses_a_package_mined_with_other_thresholds(db_session, imported):
    rule_set, covered = imported
    thresholds = {stream: THRESHOLD for stream in STREAMS}
    thresholds["teacher_day"] = {**THRESHOLD, "min_support": "0.5000"}
    package = _package(
        [_metric("teacher_subject", "key-covered", faculty_id=covered.faculty_id)],
        thresholds,
    )

    with pytest.raises(FacultyMetricsPackageError, match="teacher_day thresholds"):
        attach_faculty_metrics(db_session, rule_set.rule_set_id, package)


def test_refuses_a_package_missing_a_teacher_the_version_has_rules_for(
    db_session, imported
):
    rule_set, _covered = imported
    package = _package([_metric("teacher_subject", "someone-else", passed=False)])

    with pytest.raises(FacultyMetricsPackageError, match="not built from"):
        attach_faculty_metrics(db_session, rule_set.rule_set_id, package)


def test_refuses_faculty_ids_not_on_the_roster(db_session, imported):
    rule_set, covered = imported
    package = _package(
        [
            _metric("teacher_subject", "key-covered", faculty_id=covered.faculty_id),
            _metric("teacher_day", "ghost", faculty_id=uuid4(), passed=False),
        ]
    )

    with pytest.raises(FacultyMetricsPackageError, match="not on the roster"):
        attach_faculty_metrics(db_session, rule_set.rule_set_id, package)
