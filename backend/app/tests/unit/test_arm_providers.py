"""CHR-33 / CHR-ARM-002 guidance lookup and fixture provider tests."""

from decimal import Decimal
from uuid import UUID

import pytest

from app.services.arm import (
    FixtureRuleProvider,
    GuidanceKeyCollisionError,
    GuidanceRule,
    HistoricalGuidance,
    MinedRule,
    PatternStream,
    RuleSetNotFoundError,
)

FACULTY_A = UUID("00000000-0000-0000-0000-0000000000a1")
FACULTY_B = UUID("00000000-0000-0000-0000-0000000000b2")


def rule(
    stream: PatternStream,
    *,
    faculty_id: UUID = FACULTY_A,
    subject_code: str | None = None,
    room_code: str | None = None,
    meeting_days: str | None = None,
    time_slot: str | None = None,
    guidance_score: str = "0.5000",
) -> GuidanceRule:
    return GuidanceRule(
        stream=stream,
        faculty_id=faculty_id,
        subject_code=subject_code,
        room_code=room_code,
        meeting_days=meeting_days,
        time_slot=time_slot,
        guidance_score=Decimal(guidance_score),
        support=Decimal("0.5000"),
        confidence=Decimal("0.9000"),
        lift=Decimal("1.2000"),
        term_recurrence=2,
    )


def test_score_lookup_matches_the_full_consequent():
    guidance = HistoricalGuidance(
        rules=(
            rule(
                PatternStream.TEACHER_SUBJECT,
                subject_code="S1",
                guidance_score="0.8000",
            ),
            rule(
                PatternStream.TEACHER_SUBJECT_ROOM,
                subject_code="S1",
                room_code="R1",
                guidance_score="0.4000",
            ),
        )
    )

    assert guidance.score_for(
        PatternStream.TEACHER_SUBJECT, FACULTY_A, subject_code="S1"
    ) == Decimal("0.8000")
    assert guidance.score_for(
        PatternStream.TEACHER_SUBJECT_ROOM,
        FACULTY_A,
        subject_code="S1",
        room_code="R1",
    ) == Decimal("0.4000")
    # Right stream and instructor, different room: no rule covers it.
    assert (
        guidance.score_for(
            PatternStream.TEACHER_SUBJECT_ROOM,
            FACULTY_A,
            subject_code="S1",
            room_code="R2",
        )
        is None
    )
    assert (
        guidance.score_for(PatternStream.TEACHER_SUBJECT, FACULTY_B, subject_code="S1")
        is None
    )


def test_applicability_is_reported_rather_than_scored_as_zero():
    guidance = HistoricalGuidance(
        rules=(rule(PatternStream.TEACHER_DAY, meeting_days="MWF"),)
    )

    assert guidance.is_applicable(PatternStream.TEACHER_DAY, FACULTY_A)
    assert not guidance.is_applicable(PatternStream.TEACHER_DAY, FACULTY_B)
    assert not guidance.is_applicable(PatternStream.TEACHER_TIME, FACULTY_A)
    assert guidance.rules_for(PatternStream.TEACHER_TIME, FACULTY_A) == ()


def test_duplicate_patterns_are_rejected():
    duplicate = rule(PatternStream.TEACHER_SUBJECT, subject_code="S1")

    with pytest.raises(ValueError, match="duplicate guidance rule"):
        HistoricalGuidance(rules=(duplicate, duplicate))


def test_guidance_can_be_built_from_mined_rules():
    mined = MinedRule(
        stream=PatternStream.TEACHER_TIME,
        faculty_id=FACULTY_A,
        subject_code=None,
        room_code=None,
        meeting_days=None,
        time_slot="07:30-08:30",
        support=Decimal("0.5000"),
        confidence=Decimal("1.0000"),
        lift=Decimal("2.0000"),
        term_recurrence=2,
        guidance_score=Decimal("1.0000"),
    )

    guidance = HistoricalGuidance.from_mined([mined])

    assert guidance.rule_set_id is None
    assert guidance.score_for(
        PatternStream.TEACHER_TIME, FACULTY_A, time_slot="07:30-08:30"
    ) == Decimal("1.0000")


def test_fixture_provider_serves_its_own_set_only():
    provider = FixtureRuleProvider.from_rules(
        [rule(PatternStream.TEACHER_SUBJECT, subject_code="S1")]
    )

    assert provider.get_rules().rules[0].subject_code == "S1"
    with pytest.raises(RuleSetNotFoundError):
        provider.get_rules(UUID("00000000-0000-0000-0000-0000000000ff"))


def test_time_lookup_matches_the_same_interval_in_either_format():
    bare = HistoricalGuidance(
        rules=(rule(PatternStream.TEACHER_TIME, time_slot="07:30-09:00", guidance_score="0.7000"),)
    )
    prefixed = HistoricalGuidance(
        rules=(rule(PatternStream.TEACHER_TIME, time_slot="LEC 07:30-09:00", guidance_score="0.7000"),)
    )

    for guidance in (bare, prefixed):
        for label in ("07:30-09:00", "LEC 07:30-09:00", "LAB 07:30-09:00"):
            assert guidance.score_for(
                PatternStream.TEACHER_TIME, FACULTY_A, time_slot=label
            ) == Decimal("0.7000")
        assert guidance.score_for(PatternStream.TEACHER_TIME, FACULTY_A, time_slot="LEC 07:30-10:00") is None
        assert guidance.score_for(PatternStream.TEACHER_TIME, FACULTY_A, time_slot="lec 07:30-09:00") is None


def test_unrecognized_time_labels_only_match_themselves():
    guidance = HistoricalGuidance(rules=(rule(PatternStream.TEACHER_TIME, time_slot="<TIME-1>"),))

    assert guidance.score_for(PatternStream.TEACHER_TIME, FACULTY_A, time_slot="<TIME-1>") == Decimal("0.5000")
    assert guidance.score_for(PatternStream.TEACHER_TIME, FACULTY_A, time_slot=" <TIME-1> ") is None


@pytest.mark.parametrize(
    "labels",
    [("07:30-09:00", "LEC 07:30-09:00"), ("LAB 07:30-09:00", "07:30-09:00"), ("LEC 07:30-09:00", "LAB 07:30-09:00")],
)
def test_rules_that_collide_after_time_normalization_are_reported(labels):
    first = rule(PatternStream.TEACHER_TIME, time_slot=labels[0], guidance_score="0.4000")
    second = rule(PatternStream.TEACHER_TIME, time_slot=labels[1], guidance_score="0.9000")

    for ordered in ((first, second), (second, first)):
        with pytest.raises(GuidanceKeyCollisionError) as raised:
            HistoricalGuidance(rules=ordered)
        assert {raised.value.first.time_slot, raised.value.second.time_slot} == set(labels)


def test_equivalent_intervals_for_different_faculty_do_not_collide():
    guidance = HistoricalGuidance(
        rules=(
            rule(PatternStream.TEACHER_TIME, time_slot="07:30-09:00"),
            rule(PatternStream.TEACHER_TIME, faculty_id=FACULTY_B, time_slot="LEC 07:30-09:00"),
        )
    )

    assert guidance.score_for(PatternStream.TEACHER_TIME, FACULTY_B, time_slot="07:30-09:00") == Decimal("0.5000")


def test_exact_duplicate_time_rules_are_still_duplicates_not_collisions():
    duplicate = rule(PatternStream.TEACHER_TIME, time_slot="LEC 07:30-09:00")

    with pytest.raises(ValueError, match="duplicate guidance rule") as raised:
        HistoricalGuidance(rules=(duplicate, duplicate))
    assert not isinstance(raised.value, GuidanceKeyCollisionError)
