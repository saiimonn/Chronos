"""Per-teacher best-pattern diagnostics (Rule Sets page teacher coverage)."""

from decimal import Decimal
from itertools import count
from uuid import UUID

from app.services.arm import MiningThresholds, PatternStream, RuleMiner
from app.services.arm.faculty_metrics import (
    best_faculty_patterns,
    ga_day_pattern,
    usual_day_patterns,
)
from app.tests.unit.test_arm_mining import offering, term, training_only

FACULTY_A = UUID("00000000-0000-0000-0000-0000000000a1")
FACULTY_B = UUID("00000000-0000-0000-0000-0000000000b2")
FACULTY_C = UUID("00000000-0000-0000-0000-0000000000c3")

THRESHOLDS = MiningThresholds(
    min_support=Decimal("0.2"),
    min_confidence=Decimal("0.5"),
    min_lift=Decimal("1"),
    min_term_recurrence=2,
)

_ids = count(1)


def _offerings(faculty: UUID, subject: str, times: int, room: str = "<ROOM-1>"):
    return [
        offering(next(_ids), faculty_id=faculty, subject_code=subject, room_code=room)
        for _ in range(times)
    ]


def _dataset():
    # A teaches S1 in both terms (a rule); B teaches S2 once in one term (too
    # rare and not recurrent); C teaches S3 in both terms, but only once each.
    return (
        term(1, "2020-2021", 1, *_offerings(FACULTY_A, "S1", 3), *_offerings(FACULTY_B, "S2", 1),
             *_offerings(FACULTY_C, "S3", 1)),
        term(2, "2020-2021", 2, *_offerings(FACULTY_A, "S1", 3), *_offerings(FACULTY_C, "S3", 1)),
    )


def _by_faculty(metrics):
    return {metric.faculty_id: metric for metric in metrics}


def test_records_every_teacher_including_those_without_rules():
    data = training_only(PatternStream.TEACHER_SUBJECT, *_dataset())

    metrics = _by_faculty(
        best_faculty_patterns(data.stream, data.transactions, THRESHOLDS, 2)
    )

    assert set(metrics) == {FACULTY_A, FACULTY_B, FACULTY_C}
    a, b, c = metrics[FACULTY_A], metrics[FACULTY_B], metrics[FACULTY_C]
    # 9 transactions: A/S1 = 6, B/S2 = 1, C/S3 = 2.
    assert (a.support, a.confidence, a.term_recurrence, a.offering_count) == (
        Decimal("0.6667"), Decimal("1.0000"), 2, 6,
    )
    assert a.meets_all
    assert (b.support, b.meets_support, b.meets_term_recurrence) == (
        Decimal("0.1111"), False, False,
    )
    assert b.meets_confidence and b.meets_lift
    # C recurs in both terms but its 2 of 9 offerings fall short of 0.2 support.
    assert c.support == Decimal("0.2222")
    assert c.meets_support and c.meets_term_recurrence
    assert c.subject_code == "S3"


def test_passing_teachers_are_exactly_the_teachers_the_miner_keeps_rules_for():
    for stream in PatternStream:
        data = training_only(stream, *_dataset())
        mined = RuleMiner().mine_stream(data, THRESHOLDS)

        passing = {
            metric.faculty_id
            for metric in best_faculty_patterns(
                stream, data.transactions, THRESHOLDS, mined.training_term_count
            )
            if metric.meets_all
        }

        assert passing == {rule.faculty_id for rule in mined.rules}, stream


def test_prefers_the_pattern_that_meets_more_thresholds():
    # A teaches S1 four times in one term and S2 once in each of two terms. S1
    # is more frequent but meets no recurrence; S2 meets recurrence too.
    terms = (
        term(1, "2020-2021", 1, *_offerings(FACULTY_A, "S1", 4), *_offerings(FACULTY_A, "S2", 1)),
        term(2, "2020-2021", 2, *_offerings(FACULTY_A, "S2", 1), *_offerings(FACULTY_B, "S9", 4)),
    )
    data = training_only(PatternStream.TEACHER_SUBJECT, *terms)
    loose = MiningThresholds(
        min_support=Decimal("0.1"),
        min_confidence=Decimal("0.3"),
        min_lift=Decimal("0"),
        min_term_recurrence=2,
    )

    best = _by_faculty(best_faculty_patterns(data.stream, data.transactions, loose, 2))

    assert best[FACULTY_A].subject_code == "S2"


def test_subject_room_metrics_use_teacher_and_subject_as_antecedent():
    terms = (
        term(1, "2020-2021", 1, *_offerings(FACULTY_A, "S1", 2, room="R1"),
             *_offerings(FACULTY_A, "S2", 2, room="R2")),
        term(2, "2020-2021", 2, *_offerings(FACULTY_A, "S1", 2, room="R1")),
    )
    data = training_only(PatternStream.TEACHER_SUBJECT_ROOM, *terms)

    (metric,) = best_faculty_patterns(data.stream, data.transactions, THRESHOLDS, 2)

    assert (metric.subject_code, metric.room_code) == ("S1", "R1")
    # Every S1 offering by A is in R1, so confidence is 1.
    assert metric.confidence == Decimal("1.0000")
    assert metric.term_recurrence == 2


def test_no_transactions_yield_no_metrics():
    assert best_faculty_patterns(PatternStream.TEACHER_DAY, (), THRESHOLDS, 1) == ()


def test_ga_day_pattern_groups_split_sections_under_their_pattern():
    assert [ga_day_pattern(days) for days in ("M", "W", "MW", "T", "Th", "TTh", "F")] == [
        "MW", "MW", "MW", "TTh", "TTh", "TTh", "F",
    ]
    # Strings that fit no GA pattern are kept as they are.
    assert [ga_day_pattern(days) for days in ("MTWTh", "MWF", "Sa", "MF")] == [
        "MTWTh", "MWF", "Sa", "MF",
    ]


def test_usual_day_patterns_ranks_by_the_teachers_own_class_count():
    # The largest exact string is F (9 of 32), but the MW lines (MW 5, M 2,
    # W 3) add up to 10, so the teacher usually teaches MW.
    days = ["F"] * 9 + ["MW"] * 5 + ["M"] * 2 + ["W"] * 3
    days += ["TTh"] * 4 + ["T"] * 2 + ["Th"] * 2 + ["MTWTh"] * 3 + ["Sa", "Su"]
    assert usual_day_patterns(("a", d) for d in days) == {"a": ("MW", 10, 32)}


def test_usual_day_patterns_breaks_ties_toward_mw_then_tth():
    assert usual_day_patterns([("a", "F"), ("a", "TTh"), ("b", "Sa"), ("b", "TTh")]) == {
        "a": ("TTh", 1, 2),
        "b": ("TTh", 1, 2),
    }
