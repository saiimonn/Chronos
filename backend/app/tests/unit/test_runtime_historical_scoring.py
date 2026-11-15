"""CHR-113 / CHR-GA-004 synthetic Offering-level historical scoring tests."""

from dataclasses import asdict, replace
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import Assignment, build_offering_block_ids
from app.services.arm.preprocessing import PatternStream
from app.services.arm.providers import GuidanceRule, HistoricalGuidance
from app.services.ga.chromosome import create_individual
from app.services.ga.fitness import (
    HistoricalScoreBreakdown,
    LexicographicFitnessEvaluator,
)
from app.services.ga.rule_scoring import RuleBackedHistoricalScorer
from app.tests.support.ga import (
    FACULTY_FLEXIBLE_ID,
    FACULTY_MORNING_ID,
    LABORATORY_ROOM,
    LECTURE_ROOM,
    LECTURE_SUBJECT,
    SLOT_AFTERNOON_ID,
    SLOT_LAB_ID,
    SLOT_MORNING_ID,
    make_combined_offering_problem,
    make_ga_problem,
)
from app.tests.support.scheduling import (
    FACULTY_ID,
    make_candidate_schedule,
    make_scheduling_problem,
)

NO_HISTORY = HistoricalScoreBreakdown(
    teacher_subject_score=None,
    teacher_day_score=None,
    teacher_time_score=None,
    teacher_subject_room_score=None,
    historical_pattern_attainment=0.0,
)
UNKNOWN_ID = UUID("11300000-0000-0000-0000-000000000099")


def _rule(
    stream: PatternStream,
    score: str = "0.8",
    *,
    faculty_id: UUID = FACULTY_ID,
    **consequent: str,
) -> GuidanceRule:
    return GuidanceRule(
        stream=stream,
        faculty_id=faculty_id,
        subject_code=consequent.get("subject_code"),
        room_code=consequent.get("room_code"),
        meeting_days=consequent.get("meeting_days"),
        time_slot=consequent.get("time_slot"),
        guidance_score=Decimal(score),
        support=Decimal("0.1250"),
        confidence=Decimal("0.7500"),
        lift=Decimal("2.5000"),
        term_recurrence=3,
    )


@pytest.mark.parametrize(
    "stream, consequent, score_field, mismatch",
    (
        (
            PatternStream.TEACHER_SUBJECT,
            {"subject_code": "<SUBJECT-1>"},
            "teacher_subject_score",
            {"subject_code": "<OTHER-SUBJECT>"},
        ),
        (
            PatternStream.TEACHER_DAY,
            {"meeting_days": "<DAYS-1>"},
            "teacher_day_score",
            {"meeting_days": "<OTHER-DAYS>"},
        ),
        (
            PatternStream.TEACHER_TIME,
            {"time_slot": "<TIME-SLOT-1>"},
            "teacher_time_score",
            {"time_slot": " <TIME-SLOT-1> "},
        ),
        (
            PatternStream.TEACHER_SUBJECT_ROOM,
            {"subject_code": "<SUBJECT-1>", "room_code": "<ROOM-1>"},
            "teacher_subject_room_score",
            {"room_code": "<OTHER-ROOM>"},
        ),
    ),
    ids=("subject", "day", "exact-time", "room"),
)
def test_each_stream_rewards_exact_matches_and_scores_applicable_misses_zero(
    stream, consequent, score_field, mismatch
):
    problem = make_scheduling_problem()
    assignments = make_candidate_schedule().assignments
    rule = _rule(stream, **consequent)

    matched = RuleBackedHistoricalScorer(HistoricalGuidance((rule,))).score(
        problem, assignments
    )
    missed = RuleBackedHistoricalScorer(
        HistoricalGuidance((replace(rule, **mismatch),))
    ).score(problem, assignments)

    assert matched == replace(
        NO_HISTORY, **{score_field: 0.8}, historical_pattern_attainment=0.8
    )
    assert missed == replace(NO_HISTORY, **{score_field: 0.0})


def test_four_streams_use_normalized_rewards_and_equal_stream_weights():
    problem = make_scheduling_problem()
    rules = (
        _rule(PatternStream.TEACHER_SUBJECT, "0.8", subject_code="<SUBJECT-1>"),
        _rule(PatternStream.TEACHER_DAY, "0.6", meeting_days="<DAYS-1>"),
        _rule(PatternStream.TEACHER_TIME, "0.4", time_slot="<TIME-SLOT-1>"),
        _rule(
            PatternStream.TEACHER_SUBJECT_ROOM,
            "0.2",
            subject_code="<SUBJECT-1>",
            room_code="<ROOM-1>",
        ),
        _rule(PatternStream.TEACHER_SUBJECT, "1", subject_code="<OTHER-SUBJECT>"),
    )

    scores = RuleBackedHistoricalScorer(HistoricalGuidance(rules)).score(
        problem, make_candidate_schedule().assignments
    )

    assert asdict(scores) == pytest.approx(
        {
            "teacher_subject_score": 0.8,
            "teacher_day_score": 0.6,
            "teacher_time_score": 0.4,
            "teacher_subject_room_score": 0.2,
            "historical_pattern_attainment": 0.5,
        }
    )


def test_sparse_streams_average_only_applicable_resolved_offerings():
    problem = make_ga_problem()
    lecture = Assignment(
        class_id=problem.classes[0].class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    lab = replace(
        lecture,
        class_id=problem.classes[1].class_id,
        time_slot_id=SLOT_LAB_ID,
        room_code=LABORATORY_ROOM,
    )
    without_history = replace(
        lecture,
        class_id=problem.classes[2].class_id,
        faculty_id=FACULTY_MORNING_ID,
    )
    guidance = HistoricalGuidance(
        (
            _rule(
                PatternStream.TEACHER_SUBJECT,
                faculty_id=FACULTY_FLEXIBLE_ID,
                subject_code=LECTURE_SUBJECT,
            ),
            _rule(
                PatternStream.TEACHER_DAY,
                "0.6",
                faculty_id=FACULTY_FLEXIBLE_ID,
                meeting_days="MWF",
            ),
        )
    )
    scorer = RuleBackedHistoricalScorer(guidance)

    scores = scorer.score(problem, (lecture, lab, without_history))

    assert scores == HistoricalScoreBreakdown(0.4, 0.6, None, None, 0.5)
    assert scorer.score(problem, (lecture, lab)) == scores
    partial = scorer.score(problem, (lecture,))
    assert partial.teacher_subject_score == 0.8
    assert partial.historical_pattern_attainment == pytest.approx(0.7)


@pytest.mark.parametrize(
    "faculty_id, subject_code, expected",
    (
        (FACULTY_FLEXIBLE_ID, LECTURE_SUBJECT, 0.0),
        (FACULTY_MORNING_ID, LECTURE_SUBJECT, None),
        (FACULTY_FLEXIBLE_ID, "<SUBJECT-LAB>", None),
    ),
    ids=("unmatched-room", "missing-teacher", "missing-subject"),
)
def test_room_applicability_requires_both_teacher_and_subject(
    faculty_id, subject_code, expected
):
    problem = make_ga_problem(include_unstaffable=False)
    assignment = Assignment(
        class_id=problem.classes[0].class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    guidance = HistoricalGuidance(
        (
            _rule(
                PatternStream.TEACHER_SUBJECT_ROOM,
                faculty_id=faculty_id,
                subject_code=subject_code,
                room_code=LABORATORY_ROOM,
            ),
        )
    )

    scores = RuleBackedHistoricalScorer(guidance).score(problem, (assignment,))

    assert scores == replace(NO_HISTORY, teacher_subject_room_score=expected)


@pytest.mark.parametrize("unrelated_rules", (False, True))
def test_empty_or_unrelated_history_reports_null_streams(unrelated_rules):
    rules = ()
    if unrelated_rules:
        rules = (
            _rule(
                PatternStream.TEACHER_SUBJECT,
                faculty_id=UNKNOWN_ID,
                subject_code="<SUBJECT-1>",
            ),
        )

    assert (
        RuleBackedHistoricalScorer(HistoricalGuidance(rules)).score(
            make_scheduling_problem(), make_candidate_schedule().assignments
        )
        == NO_HISTORY
    )


def test_no_resolved_offerings_reports_null_streams_even_with_matching_history():
    guidance = HistoricalGuidance(
        (_rule(PatternStream.TEACHER_SUBJECT, subject_code="<SUBJECT-1>"),)
    )

    assert (
        RuleBackedHistoricalScorer(guidance).score(make_scheduling_problem(), ())
        == NO_HISTORY
    )


@pytest.mark.parametrize("shared_offering, expected", ((True, 0.4), (False, 1.6 / 3)))
def test_shared_blocks_count_once_but_different_offerings_count_separately(
    shared_offering, expected
):
    problem = make_combined_offering_problem()
    first, second = problem.classes
    if not shared_offering:
        second = replace(
            second,
            offering_id=UUID("11300000-0000-0000-0000-000000000001"),
            group_no=2,
        )
    lab = make_ga_problem(include_unstaffable=False).classes[1]
    classes = (first, second, lab)
    problem = replace(
        problem, classes=classes, offering_block_ids=build_offering_block_ids(classes)
    )
    lecture = Assignment(
        class_id=first.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    assignments = (
        lecture,
        replace(lecture, class_id=second.class_id),
        replace(
            lecture,
            class_id=lab.class_id,
            room_code=LABORATORY_ROOM,
            time_slot_id=SLOT_LAB_ID,
        ),
    )
    scorer = RuleBackedHistoricalScorer(
        HistoricalGuidance(
            (
                _rule(
                    PatternStream.TEACHER_SUBJECT,
                    faculty_id=FACULTY_FLEXIBLE_ID,
                    subject_code=LECTURE_SUBJECT,
                ),
            )
        )
    )

    scores = scorer.score(problem, assignments)

    assert scores.teacher_subject_score == pytest.approx(expected)
    assert scores.historical_pattern_attainment == pytest.approx(expected)
    assert scorer.score(problem, tuple(reversed(assignments))) == scores


@pytest.mark.parametrize(
    "conflict",
    (
        {"faculty_id": FACULTY_MORNING_ID},
        {"room_code": LABORATORY_ROOM},
        {"meeting_days": "TTh"},
        {"time_slot_id": SLOT_AFTERNOON_ID},
    ),
    ids=("faculty", "room", "days", "slot"),
)
def test_conflicting_shared_offering_placements_are_refused(conflict):
    problem = make_combined_offering_problem()
    first = Assignment(
        class_id=problem.classes[0].class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    second = replace(first, class_id=problem.classes[1].class_id, **conflict)

    with pytest.raises(ValueError, match="inconsistent historical-score placements"):
        RuleBackedHistoricalScorer(HistoricalGuidance(())).score(
            problem, (first, second)
        )


@pytest.mark.parametrize("field", ("class_id", "time_slot_id"))
def test_unknown_class_or_slot_is_refused(field):
    assignment = replace(
        make_candidate_schedule().assignments[0], **{field: UNKNOWN_ID}
    )

    with pytest.raises(ValueError, match="unknown class or slot"):
        RuleBackedHistoricalScorer(HistoricalGuidance(())).score(
            make_scheduling_problem(), (assignment,)
        )


def test_new_scorer_preserves_first_two_components_and_lexicographic_ranking():
    problem = make_scheduling_problem()
    second_class = replace(
        problem.classes[0],
        class_id=UUID("11300000-0000-0000-0000-000000000002"),
        offering_id=UUID("11300000-0000-0000-0000-000000000003"),
        group_no=2,
    )
    classes = (*problem.classes, second_class)
    problem = replace(
        problem, classes=classes, offering_block_ids=build_offering_block_ids(classes)
    )
    first = replace(make_candidate_schedule().assignments[0], meeting_days="MWF")
    second = replace(first, class_id=second_class.class_id)
    scorer = RuleBackedHistoricalScorer(
        HistoricalGuidance((_rule(PatternStream.TEACHER_DAY, "1", meeting_days="MWF"),))
    )
    baseline = LexicographicFitnessEvaluator(problem)
    guided = LexicographicFitnessEvaluator(problem, scorer)
    placements = {
        "incomplete": (first,),
        "complete_conflicted": (first, second),
        "clean_guided": (first, replace(second, meeting_days="TTh")),
        "clean_unguided": (
            replace(first, meeting_days="M"),
            replace(second, meeting_days="T"),
        ),
    }
    individuals = {}
    for name, assignments in placements.items():
        individual = create_individual(problem, assignments)
        values = guided(individual)
        assert values[:2] == baseline(individual)[:2]
        individual.fitness.values = values
        individuals[name] = individual

    assert individuals["incomplete"].fitness.values[2] == 1.0
    assert individuals["complete_conflicted"].fitness.values[1] < 0
    assert (
        individuals["complete_conflicted"].fitness > individuals["incomplete"].fitness
    )
    assert individuals["clean_unguided"].fitness.values[2] == 0.0
    assert (
        individuals["clean_unguided"].fitness
        > individuals["complete_conflicted"].fitness
    )
    assert (
        individuals["clean_guided"].fitness.values[:2]
        == (individuals["clean_unguided"].fitness.values[:2])
    )
    assert individuals["clean_guided"].fitness > individuals["clean_unguided"].fitness


def test_operational_slot_labels_match_bare_historical_time_rules():
    problem = make_scheduling_problem()
    problem = replace(
        problem,
        time_slots=tuple(replace(slot, label="LEC 07:30-09:00") for slot in problem.time_slots),
    )
    assignments = make_candidate_schedule().assignments

    matched = RuleBackedHistoricalScorer(
        HistoricalGuidance((_rule(PatternStream.TEACHER_TIME, time_slot="07:30-09:00"),))
    ).score(problem, assignments)
    other_end = RuleBackedHistoricalScorer(
        HistoricalGuidance((_rule(PatternStream.TEACHER_TIME, time_slot="07:30-10:00"),))
    ).score(problem, assignments)

    assert matched.teacher_time_score == 0.8
    assert other_end.teacher_time_score == 0.0
