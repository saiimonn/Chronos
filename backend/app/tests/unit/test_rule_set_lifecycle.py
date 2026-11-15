"""Unit tests for rule set structural validation and lifecycle logic (CHR-RBAC-004)."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import APIError
from app.models.extracted_rule import PATTERN_TYPES
from app.services.rule_sets import validate_rule_set


def _make_threshold(
    pattern_type: str,
    min_support: Decimal = Decimal("0.1"),
    min_confidence: Decimal = Decimal("0.2"),
    min_lift: Decimal = Decimal("1.0"),
    min_term_recurrence: int = 1,
    earliest_academic_year: str = "2020-2021",
):
    return SimpleNamespace(
        pattern_type=pattern_type,
        min_support=min_support,
        min_confidence=min_confidence,
        min_lift=min_lift,
        min_term_recurrence=min_term_recurrence,
        earliest_academic_year=earliest_academic_year,
    )


def _make_rule(
    pattern_type: str = "teacher_subject",
    faculty_id=None,
    subject_code="CS101",
    room_code=None,
    meeting_days=None,
    time_slot=None,
    support=Decimal("0.5"),
    confidence=Decimal("0.8"),
    lift=Decimal("1.2"),
    term_recurrence=2,
    guidance_score=Decimal("0.75"),
):
    if faculty_id is None:
        faculty_id = uuid4()
    return SimpleNamespace(
        rule_id=uuid4(),
        pattern_type=pattern_type,
        faculty_id=faculty_id,
        subject_code=subject_code,
        room_code=room_code,
        meeting_days=meeting_days,
        time_slot=time_slot,
        support=support,
        confidence=confidence,
        lift=lift,
        term_recurrence=term_recurrence,
        guidance_score=guidance_score,
    )


def _make_rule_set(
    included_terms=None,
    thresholds=None,
    extracted_rules=None,
):
    if included_terms is None:
        included_terms = [SimpleNamespace(term_id=uuid4(), academic_year="2025-2026", term_number=1)]
    if thresholds is None:
        thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    if extracted_rules is None:
        extracted_rules = [
            _make_rule("teacher_subject", subject_code="CS101"),
            _make_rule("teacher_day", subject_code=None, meeting_days="MWF"),
            _make_rule("teacher_time", subject_code=None, time_slot="07:30-08:30"),
            _make_rule("teacher_subject_room", subject_code="CS101", room_code="LB101"),
        ]
    return SimpleNamespace(
        rule_set_id=uuid4(),
        version=1,
        status="draft",
        included_terms=included_terms,
        thresholds=thresholds,
        extracted_rules=extracted_rules,
    )


def test_validate_rule_set_succeeds_on_valid_complete_rule_set():
    rs = _make_rule_set()
    validate_rule_set(rs)  # should not raise


def test_validate_rule_set_fails_on_empty_included_terms():
    rs = _make_rule_set(included_terms=[])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "term" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_missing_threshold_stream():
    # Only 3 thresholds instead of 4
    thresholds = [_make_threshold("teacher_subject"), _make_threshold("teacher_day"), _make_threshold("teacher_time")]
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "four required threshold streams" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_invalid_min_support():
    thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    thresholds[0].min_support = Decimal("0.0")
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "min_support" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_invalid_min_confidence():
    thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    thresholds[0].min_confidence = Decimal("1.5")
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "min_confidence" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_negative_min_lift():
    thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    thresholds[0].min_lift = Decimal("-0.1")
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "min_lift" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_non_positive_min_term_recurrence():
    thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    thresholds[0].min_term_recurrence = 0
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "min_term_recurrence" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_empty_earliest_academic_year():
    thresholds = [_make_threshold(pt) for pt in PATTERN_TYPES]
    thresholds[0].earliest_academic_year = "   "
    rs = _make_rule_set(thresholds=thresholds)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "earliest_academic_year" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_unrecognized_pattern_type():
    rules = [_make_rule("unknown_stream")]
    rs = _make_rule_set(extracted_rules=rules)
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "unrecognized pattern_type" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_missing_faculty_id():
    rule = _make_rule("teacher_subject", subject_code="CS101")
    rule.faculty_id = None
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "faculty_id" in exc_info.value.message.lower()


@pytest.mark.parametrize(
    ("pattern_type", "subject_code", "room_code", "meeting_days", "time_slot"),
    [
        ("teacher_subject", None, None, None, None),
        ("teacher_subject", "CS101", "LB101", None, None),
        ("teacher_day", None, None, None, None),
        ("teacher_day", "CS101", None, "MWF", None),
        ("teacher_time", None, None, None, None),
        ("teacher_time", None, None, "MWF", "07:30-08:30"),
        ("teacher_subject_room", "CS101", None, None, None),
        ("teacher_subject_room", None, "LB101", None, None),
        ("teacher_subject_room", "CS101", "LB101", "MWF", None),
    ],
)
def test_validate_rule_set_fails_on_invalid_consequents(
    pattern_type, subject_code, room_code, meeting_days, time_slot
):
    rule = _make_rule(
        pattern_type=pattern_type,
        subject_code=subject_code,
        room_code=room_code,
        meeting_days=meeting_days,
        time_slot=time_slot,
    )
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "consequent" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_invalid_support():
    rule = _make_rule(support=Decimal("0.0"))
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "support" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_invalid_confidence():
    rule = _make_rule(confidence=Decimal("1.2"))
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "confidence" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_negative_lift():
    rule = _make_rule(lift=Decimal("-0.5"))
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "lift" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_non_positive_term_recurrence():
    rule = _make_rule(term_recurrence=0)
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert "term_recurrence" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_invalid_guidance_score():
    rule = _make_rule(guidance_score=Decimal("-0.1"))
    rs = _make_rule_set(extracted_rules=[rule])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "RULE_SET_INVALID"
    assert "guidance_score" in exc_info.value.message.lower()


def test_validate_rule_set_fails_on_empty_extracted_rules():
    rs = _make_rule_set(extracted_rules=[])
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "RULE_SET_INVALID"
    assert "rule" in exc_info.value.message.lower()



def test_validate_rule_set_fails_when_time_slots_collide_after_normalization():
    # The GA indexes "LEC 07:30-09:00" and "07:30-09:00" as one pattern, so
    # activating both would hand it guidance it refuses to load.
    faculty_id = uuid4()
    rs = _make_rule_set(
        extracted_rules=[
            _make_rule("teacher_time", faculty_id, subject_code=None, time_slot=slot)
            for slot in ("07:30-09:00", "LEC 07:30-09:00")
        ]
    )
    with pytest.raises(APIError) as exc_info:
        validate_rule_set(rs)
    assert exc_info.value.code == "RULE_SET_INVALID"
    assert "runtime guidance" in exc_info.value.message
