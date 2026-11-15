"""CHR-113 / CHR-GA-004 synthetic guidance snapshots and real worker GA loops."""

import json
import sys
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from app.domain import (
    BlockGenerationContext,
    BlockSectionDTO,
    LockedAssignment,
    SubjectType,
    build_offering_block_ids,
)
from app.services.arm.preprocessing import PatternStream
from app.services.arm.providers import (
    DatabaseRuleProvider,
    FixtureRuleProvider,
    GuidanceRule,
    HistoricalGuidance,
)
from app.services.ga.config import GAConfig
from app.services.ga.rule_scoring import SCORING_POLICY
from app.services.jobs.codecs import (
    CODEC_VERSION,
    decode_guidance_snapshot,
    encode_block_ga_payload,
    encode_ga_payload,
    encode_guidance_snapshot,
)
from app.services.jobs.handlers import run_ga
from app.tests.support.scheduling import (
    FACULTY_ID,
    SCHEDULE_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
)

RULE_SET_ID = UUID("11300000-0000-0000-0000-000000000010")
RULE_SET_VERSION = 11
JOB_ID = UUID("11300000-0000-0000-0000-000000000011")


def _guidance() -> HistoricalGuidance:
    subject = GuidanceRule(
        stream=PatternStream.TEACHER_SUBJECT,
        faculty_id=FACULTY_ID,
        subject_code="<SUBJECT-1>",
        room_code=None,
        meeting_days=None,
        time_slot=None,
        guidance_score=Decimal("0.1234567890123456789012345670"),
        support=Decimal("0.1250"),
        confidence=Decimal("0.7500"),
        lift=Decimal("2.5000"),
        term_recurrence=3,
    )
    return HistoricalGuidance(
        rules=(
            subject,
            replace(
                subject,
                stream=PatternStream.TEACHER_DAY,
                subject_code=None,
                meeting_days="MW",
                guidance_score=Decimal("0.7500"),
            ),
            replace(
                subject,
                stream=PatternStream.TEACHER_TIME,
                subject_code=None,
                time_slot="<TIME-SLOT-1>",
                guidance_score=Decimal("0.2500"),
            ),
            replace(
                subject,
                stream=PatternStream.TEACHER_SUBJECT_ROOM,
                room_code="<ROOM-1>",
                guidance_score=Decimal("0.5000"),
            ),
        ),
        rule_set_id=RULE_SET_ID,
        version=RULE_SET_VERSION,
    )


def _worker_payload(scope: str) -> dict:
    problem = make_scheduling_problem()
    unstaffable = replace(
        problem.classes[0],
        class_id=UUID("11300000-0000-0000-0000-000000000020"),
        offering_id=UUID("11300000-0000-0000-0000-000000000021"),
        subject_code="<SUBJECT-UNSTAFFABLE>",
    )
    classes = (*problem.classes, unstaffable)
    # No 150-minute slot exists, so the unresolved lab forces the child loop.
    problem = replace(
        problem,
        classes=classes,
        subjects=(
            *problem.subjects,
            replace(
                problem.subjects[0],
                subject_code=unstaffable.subject_code,
                subject_type=SubjectType.LAB,
            ),
        ),
        offering_block_ids=build_offering_block_ids(classes),
    )
    config = GAConfig(
        population_size=4,
        max_generations=2,
        tournament_size=2,
        elite_count=1,
        stagnation_limit=None,
        random_seed=17,
        meeting_day_patterns=("MW",),
    )
    if scope == "term":
        payload = encode_ga_payload(problem, config)
    else:
        selected = problem.classes[0]
        context = BlockGenerationContext(
            term=problem.term,
            selected_block_id=selected.block_id,
            selected_block=BlockSectionDTO(
                block_id=selected.block_id,
                term_id=selected.term_id,
                program_code=selected.program_code,
                year_level=selected.year_level,
                section_letter=selected.section_letter,
                capacity=selected.capacity,
            ),
            selected_classes=problem.classes,
            locked_assignments=(
                LockedAssignment(
                    class_id=UUID("11300000-0000-0000-0000-000000000030"),
                    block_id=UUID("11300000-0000-0000-0000-000000000031"),
                    faculty_id=FACULTY_ID,
                    time_slot_id=TIME_SLOT_ID,
                    room_code="<ROOM-1>",
                    meeting_days="Sa",
                    offering_id=UUID("11300000-0000-0000-0000-000000000032"),
                    units=3,
                ),
            ),
            faculty=problem.faculty,
            subjects=problem.subjects,
            rooms=problem.rooms,
            programs=problem.programs,
            time_slots=problem.time_slots,
            offering_block_ids=problem.offering_block_ids,
            base_schedule_id=SCHEDULE_ID,
        )
        payload = encode_block_ga_payload(context, config)
    payload["job_id"] = str(JOB_ID)
    return payload


def test_guidance_snapshot_json_round_trip_preserves_decimal_scale_and_identity():
    guidance = _guidance()
    snapshot = json.loads(json.dumps(encode_guidance_snapshot(guidance)))

    decoded = decode_guidance_snapshot(
        {"version": CODEC_VERSION, "historical_guidance": snapshot}
    )

    assert decoded == guidance
    assert decoded.rule_set_id == RULE_SET_ID
    assert decoded.version == RULE_SET_VERSION
    assert snapshot["mode"] == "guided"
    assert snapshot["scoring_policy"] == SCORING_POLICY
    assert encode_guidance_snapshot(decoded) == snapshot
    for original, restored in zip(guidance.rules, decoded.rules, strict=True):
        for field in ("guidance_score", "support", "confidence", "lift"):
            value = getattr(restored, field)
            assert isinstance(value, Decimal)
            assert value.as_tuple() == getattr(original, field).as_tuple()


@pytest.mark.parametrize("scope", ("term", "block"))
def test_ga_encoders_default_to_explicit_unguided_snapshot(scope):
    payload = _worker_payload(scope)

    assert CODEC_VERSION == payload["version"] == 7
    assert payload["historical_guidance"] == {
        "mode": "unguided",
        "scoring_policy": SCORING_POLICY,
        "rule_set_id": None,
        "rule_set_version": None,
        "rules": [],
    }
    assert decode_guidance_snapshot(payload) is None


@pytest.mark.parametrize(
    "payload",
    (
        {"version": 7},
        {"version": 7, "historical_guidance": None},
        {"version": 7, "historical_guidance": {}},
        {"version": 7, "historical_guidance": []},
    ),
    ids=("missing", "null", "incomplete", "wrong-type"),
)
def test_version_seven_requires_an_explicit_valid_guidance_snapshot(payload):
    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot(payload)


@pytest.mark.parametrize("version", range(1, 7))
def test_legacy_versions_ignore_even_present_or_malformed_guidance(version):
    for extra in (
        {},
        {"historical_guidance": encode_guidance_snapshot(_guidance())},
        {"historical_guidance": {"mode": "invalid"}},
    ):
        assert decode_guidance_snapshot({"version": version, **extra}) is None


@pytest.mark.parametrize(
    "changes",
    (
        {"guidance_score": "-0.1"},
        {"guidance_score": "1.1"},
        {"guidance_score": "NaN"},
        {"guidance_score": "not-a-decimal"},
        {"support": "1.1"},
        {"confidence": "1.1"},
        {"lift": "-0.1"},
        {"lift": "Infinity"},
        {"term_recurrence": 0},
        {"term_recurrence": True},
    ),
    ids=(
        "negative-score",
        "score-above-one",
        "nonfinite-score",
        "malformed-score",
        "support-above-one",
        "confidence-above-one",
        "negative-lift",
        "nonfinite-lift",
        "zero-recurrence",
        "boolean-recurrence",
    ),
)
def test_snapshot_rejects_invalid_rule_metrics(changes):
    snapshot = encode_guidance_snapshot(_guidance())
    snapshot["rules"][0].update(changes)

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


def test_snapshot_accepts_normalized_metric_boundaries_and_lift_above_one():
    guidance = _guidance()
    guidance = replace(
        guidance,
        rules=(
            replace(
                guidance.rules[0],
                guidance_score=Decimal(0),
                support=Decimal(0),
                confidence=Decimal(1),
                lift=Decimal("12.5000"),
                term_recurrence=1,
            ),
        ),
    )

    assert (
        decode_guidance_snapshot(
            {"version": 7, "historical_guidance": encode_guidance_snapshot(guidance)}
        )
        == guidance
    )


@pytest.mark.parametrize(
    "rule_index, changes",
    (
        (0, {"subject_code": None}),
        (0, {"meeting_days": "MW"}),
        (0, {"subject_code": "  "}),
        (0, {"stream": "unknown-stream"}),
        (3, {"subject_code": None}),
    ),
    ids=(
        "missing-consequent",
        "extra-consequent",
        "blank-consequent",
        "unknown-stream",
        "room-without-subject-antecedent",
    ),
)
def test_snapshot_rejects_malformed_stream_shapes(rule_index, changes):
    snapshot = encode_guidance_snapshot(_guidance())
    snapshot["rules"][rule_index].update(changes)

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


@pytest.mark.parametrize(
    "field, value",
    (
        ("faculty_id", "not-a-uuid"),
        ("faculty_id", "00000000-0000-0000-0000-000000000000"),
        ("rule_set_id", "not-a-uuid"),
        ("rule_set_id", "00000000-0000-0000-0000-000000000000"),
    ),
)
def test_snapshot_rejects_invalid_or_nil_identity_uuids(field, value):
    snapshot = encode_guidance_snapshot(_guidance())
    target = snapshot["rules"][0] if field == "faculty_id" else snapshot
    target[field] = value

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


@pytest.mark.parametrize(
    "changes",
    (
        {"mode": "automatic"},
        {"scoring_policy": "weighted-sum"},
        {"rule_set_id": None},
        {"rule_set_version": 0},
        {"rule_set_version": True},
        {"rules": []},
        {"rules": {}},
    ),
    ids=(
        "mode",
        "policy",
        "missing-identity",
        "version-zero",
        "boolean-version",
        "empty-guided-rules",
        "rules-not-a-list",
    ),
)
def test_snapshot_rejects_inconsistent_guided_metadata(changes):
    snapshot = encode_guidance_snapshot(_guidance())
    snapshot.update(changes)

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


@pytest.mark.parametrize("field", ("rules", "rule_set_id", "rule_set_version"))
def test_unguided_snapshot_cannot_carry_guided_rules_or_identity(field):
    snapshot = encode_guidance_snapshot(None)
    snapshot[field] = encode_guidance_snapshot(_guidance())[field]

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


def test_snapshot_rejects_duplicate_rule_keys_even_when_rewards_differ():
    snapshot = encode_guidance_snapshot(_guidance())
    snapshot["rules"].append({**snapshot["rules"][0], "guidance_score": "0.9"})

    with pytest.raises(ValueError, match="invalid historical guidance snapshot"):
        decode_guidance_snapshot({"version": 7, "historical_guidance": snapshot})


@pytest.fixture(autouse=True)
def _no_prior_publication():
    with patch(
        "app.services.jobs.handlers._published_ga_result", return_value=None
    ):
        yield


def _publish(_candidate, _job_id, _claim_token, output):
    return {**output, "candidate": {**output["candidate"], "schedule_id": str(SCHEDULE_ID)}}


@pytest.mark.parametrize("scope", ("term", "block"))
def test_workers_use_snapshot_scores_in_fitness_and_emit_guidance_diagnostics(scope):
    payload = _worker_payload(scope)
    guidance = _guidance()
    with patch(
        "app.services.jobs.handlers._persist_ga_schedule", side_effect=_publish
    ) as persist:
        unguided = run_ga(payload)
        payload["historical_guidance"] = encode_guidance_snapshot(guidance)
        guided = run_ga(payload)
        replay = run_ga(payload)

    expected_streams = {
        "teacher_subject_score": float(guidance.rules[0].guidance_score),
        "teacher_day_score": 0.75,
        "teacher_time_score": 0.25,
        "teacher_subject_room_score": 0.5,
    }
    expected_attainment = sum(expected_streams.values()) / 4
    diagnostics = guided["historical_guidance"]
    assert guided["version"] == CODEC_VERSION
    assert diagnostics["mode"] == "guided"
    assert diagnostics["rule_set_id"] == str(RULE_SET_ID)
    assert diagnostics["rule_set_version"] == RULE_SET_VERSION
    assert diagnostics["rule_count"] == len(guidance.rules)
    assert diagnostics["scores"] == pytest.approx(
        {**expected_streams, "historical_pattern_attainment": expected_attainment}
    )
    assert unguided["historical_guidance"] == {
        "scoring_policy": "equal_applicable_streams_v1",
        "mode": "unguided",
        "rule_set_id": None,
        "rule_set_version": None,
        "rule_count": 0,
        "scores": {
            **dict.fromkeys(expected_streams),
            "historical_pattern_attainment": 0.0,
        },
    }
    assert guided["metrics"]["random_seed"] == 17
    assert guided["metrics"]["required_class_count"] == 2
    assert guided["metrics"]["assigned_class_count"] == 1
    assert guided["metrics"]["assignment_completeness"] == 0.5
    assert guided["metrics"]["historical_pattern_attainment"] == pytest.approx(
        expected_attainment
    )
    assert len(guided["metrics"]["generations"]) == 3
    for guided_generation, baseline_generation in zip(
        guided["metrics"]["generations"],
        unguided["metrics"]["generations"],
        strict=True,
    ):
        assert (
            guided_generation["best_fitness"][:2]
            == (baseline_generation["best_fitness"][:2])
        )
        assert guided_generation["best_fitness"][2] == pytest.approx(
            expected_attainment
        )
        assert baseline_generation["best_fitness"][2] == 0.0
    for key in (
        "random_seed",
        "termination_reason",
        "termination_generation",
        "assignment_completeness",
        "hard_conflict_count",
        "historical_pattern_attainment",
        "generations",
    ):
        assert replay["metrics"][key] == guided["metrics"][key]
    assert replay["historical_guidance"] == diagnostics
    if scope == "term":
        assert persist.call_count == 3
        assert all(call.args[1] == JOB_ID for call in persist.call_args_list)
        assert guided["candidate"]["schedule_id"] == str(SCHEDULE_ID)
    else:
        persist.assert_not_called()
        assert guided["locks_enforced"] is True


@pytest.mark.parametrize("scope", ("term", "block"))
def test_child_loop_never_queries_live_providers_after_guidance_is_frozen(scope):
    live_provider = FixtureRuleProvider(_guidance())
    payload = _worker_payload(scope)
    frozen = live_provider.get_rules()
    payload["historical_guidance"] = encode_guidance_snapshot(frozen)
    changed = replace(
        frozen,
        rule_set_id=UUID("11300000-0000-0000-0000-000000000012"),
        version=RULE_SET_VERSION + 1,
        rules=tuple(replace(rule, guidance_score=Decimal(0)) for rule in frozen.rules),
    )
    provider_entry_points = {
        DatabaseRuleProvider.get_rules.__code__,
        FixtureRuleProvider.get_rules.__code__,
    }
    provider_calls = []

    # Observe entry without mocking providers, the scorer, or the real GA loop.
    def refuse_live_lookup(frame, event, _arg):
        if event == "call" and frame.f_code in provider_entry_points:
            provider_calls.append(frame.f_code.co_name)
            raise AssertionError("child loop queried a live guidance provider")

    previous_profile = sys.getprofile()
    with patch(
        "app.services.jobs.handlers._persist_ga_schedule", side_effect=_publish
    ):
        try:
            sys.setprofile(refuse_live_lookup)
            before = run_ga(payload)
            live_provider._guidance = changed
            after = run_ga(payload)
        finally:
            sys.setprofile(previous_profile)

    assert live_provider.get_rules() == changed
    assert provider_calls == []
    assert before["historical_guidance"] == after["historical_guidance"]
    assert after["historical_guidance"]["rule_set_id"] == str(frozen.rule_set_id)
    assert after["historical_guidance"]["rule_set_version"] == frozen.version
    assert after["metrics"]["historical_pattern_attainment"] > 0
    assert before["metrics"]["generations"] == after["metrics"]["generations"]
    assert len(after["metrics"]["generations"]) == 3


@pytest.mark.parametrize("scope", ("term", "block"))
def test_legacy_workers_ignore_extra_guidance_and_keep_unguided_fitness(scope):
    payload = _worker_payload(scope)
    payload["version"] = 6
    payload["historical_guidance"] = encode_guidance_snapshot(_guidance())

    with patch(
        "app.services.jobs.handlers._persist_ga_schedule", side_effect=_publish
    ):
        output = run_ga(payload)

    diagnostics = output["historical_guidance"]
    assert diagnostics["mode"] == "unguided"
    assert diagnostics["rule_set_id"] is None
    assert diagnostics["rule_set_version"] is None
    assert diagnostics["rule_count"] == 0
    assert diagnostics["scores"]["historical_pattern_attainment"] == 0.0
    assert all(
        score is None
        for key, score in diagnostics["scores"].items()
        if key != "historical_pattern_attainment"
    )
    assert output["metrics"]["historical_pattern_attainment"] == 0.0
    assert all(
        generation["best_fitness"][2] == 0.0
        for generation in output["metrics"]["generations"]
    )


@pytest.mark.parametrize("scope", ("term", "block"))
def test_version_seven_workers_refuse_missing_guidance_before_persistence(scope):
    payload = _worker_payload(scope)
    del payload["historical_guidance"]

    with (
        patch("app.services.jobs.handlers._persist_ga_schedule") as persist,
        pytest.raises(ValueError, match="invalid historical guidance snapshot"),
    ):
        run_ga(payload)
    persist.assert_not_called()
