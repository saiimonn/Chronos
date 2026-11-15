"""CHR-33 / CHR-ARM-002 Apriori, filtering, recurrence, and scoring tests.

Every dataset here is small enough that support, confidence, lift, recurrence,
and the guidance score are calculated by hand in the assertions.
"""

from decimal import Decimal
from uuid import UUID

import pytest

from app.services.arm import (
    MiningMode,
    HistoricalOffering,
    HistoricalPartition,
    HistoricalTerm,
    MiningSkipReason,
    MiningThresholds,
    PatternStream,
    RuleMiner,
    StreamTrainingData,
    TransactionBuilder,
    guidance_score,
    prepare_training_data,
)

FACULTY_A = UUID("00000000-0000-0000-0000-0000000000a1")
FACULTY_B = UUID("00000000-0000-0000-0000-0000000000b2")

PERMISSIVE = MiningThresholds(
    min_support=Decimal("0.2"),
    min_confidence=Decimal("0.5"),
    min_lift=Decimal("0"),
    min_term_recurrence=1,
)


def offering(
    number: int,
    *,
    faculty_id: UUID = FACULTY_A,
    subject_code: str = "<SUBJECT-1>",
    meeting_days: str = "<DAYS-1>",
    time_slot: str = "<TIME-1>",
    room_code: str | None = "<ROOM-1>",
) -> HistoricalOffering:
    return HistoricalOffering(
        historical_record_id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
        faculty_id=faculty_id,
        subject_code=subject_code,
        meeting_days=meeting_days,
        time_slot=time_slot,
        room_code=room_code,
    )


def term(
    number: int,
    academic_year: str,
    term_number: int,
    *offerings: HistoricalOffering,
) -> HistoricalTerm:
    return HistoricalTerm(
        snapshot_id=UUID(f"10000000-0000-0000-0000-{number:012d}"),
        academic_year=academic_year,
        term_number=term_number,
        offerings=offerings,
    )


def training_only(
    stream: PatternStream, *terms: HistoricalTerm
) -> StreamTrainingData:
    """Treat the given terms as the training partition, holdout already removed."""
    return StreamTrainingData(
        stream=stream,
        partition=HistoricalPartition(training_terms=terms, holdout_terms=()),
        transactions=TransactionBuilder().build(terms, stream),
    )


def only_rule(result):
    assert len(result.rules) == 1, result.rules
    return result.rules[0]


def test_teacher_subject_metrics_match_hand_calculation():
    # Four baskets: A/S1 twice in term one, A/S1 and B/S2 in term two.
    data = training_only(
        PatternStream.TEACHER_SUBJECT,
        term(
            1,
            "2018-2019",
            1,
            offering(1, subject_code="S1"),
            offering(2, subject_code="S1"),
        ),
        term(
            2,
            "2019-2020",
            1,
            offering(3, subject_code="S1"),
            offering(4, faculty_id=FACULTY_B, subject_code="S2"),
        ),
    )

    result = RuleMiner().mine_stream(data, PERMISSIVE)

    assert result.transaction_count == 4
    assert result.training_term_count == 2
    assert result.skipped_reason is None
    by_faculty = {rule.faculty_id: rule for rule in result.rules}
    assert set(by_faculty) == {FACULTY_A, FACULTY_B}

    strong = by_faculty[FACULTY_A]
    assert strong.subject_code == "S1"
    assert (strong.meeting_days, strong.time_slot, strong.room_code) == (
        None,
        None,
        None,
    )
    # support(A,S1) = 3/4; confidence = 0.75 / 0.75; lift = 1 / support(S1) = 1/0.75
    assert strong.support == Decimal("0.7500")
    assert strong.confidence == Decimal("1.0000")
    assert strong.lift == Decimal("1.3333")
    # Seen in both training terms, so persistence is 2/2.
    assert strong.term_recurrence == 2
    assert strong.guidance_score == Decimal("1.0000")

    rare = by_faculty[FACULTY_B]
    assert rare.support == Decimal("0.2500")
    assert rare.confidence == Decimal("1.0000")
    assert rare.lift == Decimal("4.0000")
    assert rare.term_recurrence == 1
    # Perfectly confident, but held in one of two terms: 1.0 * 1/2.
    assert rare.guidance_score == Decimal("0.5000")


def test_teacher_subject_room_conditions_on_the_subject():
    # Every basket is A/S1; the room differs. The stream must be mined as
    # faculty + subject -> room, so confidence is 3/4 over support(A,S1) = 1.
    data = training_only(
        PatternStream.TEACHER_SUBJECT_ROOM,
        term(
            1,
            "2023-2024",
            1,
            offering(1, subject_code="S1", room_code="R1"),
            offering(2, subject_code="S1", room_code="R1"),
        ),
        term(
            2,
            "2024-2025",
            1,
            offering(3, subject_code="S1", room_code="R1"),
            offering(4, subject_code="S1", room_code="R2"),
        ),
    )

    result = RuleMiner().mine_stream(
        data,
        MiningThresholds(
            min_support=Decimal("0.5"),
            min_confidence=Decimal("0.5"),
            min_lift=Decimal("0"),
            min_term_recurrence=1,
        ),
    )

    rule = only_rule(result)
    assert (rule.faculty_id, rule.subject_code, rule.room_code) == (
        FACULTY_A,
        "S1",
        "R1",
    )
    assert rule.support == Decimal("0.7500")
    assert rule.confidence == Decimal("0.7500")
    assert rule.lift == Decimal("1.0000")


def test_teacher_day_and_time_streams_carry_only_their_consequent():
    days = only_rule(
        RuleMiner().mine_stream(
            training_only(
                PatternStream.TEACHER_DAY,
                term(1, "2023-2024", 1, offering(1, meeting_days="MWF")),
            ),
            PERMISSIVE,
        )
    )
    times = only_rule(
        RuleMiner().mine_stream(
            training_only(
                PatternStream.TEACHER_TIME,
                term(1, "2023-2024", 1, offering(1, time_slot="07:30-08:30")),
            ),
            PERMISSIVE,
        )
    )

    assert days.meeting_days == "MWF"
    assert (days.subject_code, days.room_code, days.time_slot) == (None, None, None)
    assert times.time_slot == "07:30-08:30"
    assert (times.subject_code, times.room_code, times.meeting_days) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "thresholds",
    [
        # support(B,S2) is 0.25, below the floor.
        MiningThresholds(
            min_support=Decimal("0.5"),
            min_confidence=Decimal("0.5"),
            min_lift=Decimal("0"),
            min_term_recurrence=1,
        ),
        # lift(B,S2) is 4.0 and lift(A,S1) is 1.3333; only the rare rule clears 2.
        MiningThresholds(
            min_support=Decimal("0.2"),
            min_confidence=Decimal("0.5"),
            min_lift=Decimal("2"),
            min_term_recurrence=1,
        ),
        # B's pattern occurs in one term only.
        MiningThresholds(
            min_support=Decimal("0.2"),
            min_confidence=Decimal("0.5"),
            min_lift=Decimal("0"),
            min_term_recurrence=2,
        ),
    ],
)
def test_each_threshold_removes_rules_on_its_own(thresholds):
    data = training_only(
        PatternStream.TEACHER_SUBJECT,
        term(
            1,
            "2018-2019",
            1,
            offering(1, subject_code="S1"),
            offering(2, subject_code="S1"),
        ),
        term(
            2,
            "2019-2020",
            1,
            offering(3, subject_code="S1"),
            offering(4, faculty_id=FACULTY_B, subject_code="S2"),
        ),
    )

    retained = RuleMiner().mine_stream(data, thresholds).rules

    assert len(retained) == 1


def test_recurrence_counts_terms_not_repetitions():
    # Twelve identical baskets, all inside one term.
    repeated = term(
        1,
        "2023-2024",
        1,
        *(offering(number, subject_code="S1") for number in range(1, 13)),
    )

    rule = only_rule(
        RuleMiner().mine_stream(
            training_only(PatternStream.TEACHER_SUBJECT, repeated), PERMISSIVE
        )
    )

    assert rule.term_recurrence == 1


def _holdout_history() -> tuple[HistoricalTerm, ...]:
    # Training: two S1 terms. Holdout: the complete 2024-2025 year, all S9.
    # 2025-2026 is incomplete, so evaluation reports it and never mines it.
    return (
        term(1, "2018-2019", 1, offering(1, subject_code="S1")),
        term(2, "2019-2020", 1, offering(2, subject_code="S1")),
        *(
            term(
                2 + number,
                "2024-2025",
                number,
                offering(2 + number, faculty_id=FACULTY_B, subject_code="S9"),
            )
            for number in (1, 2, 3)
        ),
        term(6, "2025-2026", 1, offering(6, faculty_id=FACULTY_B, subject_code="S9")),
    )


def test_holdout_terms_do_not_reach_the_miner():
    candidate = RuleMiner().mine(
        prepare_training_data(_holdout_history(), mode=MiningMode.EVALUATION),
        PERMISSIVE,
    )
    subjects = candidate.result_for(PatternStream.TEACHER_SUBJECT)

    assert {rule.subject_code for rule in subjects.rules} == {"S1"}
    assert subjects.training_term_count == 2


def test_operational_mining_includes_the_holdout_and_newer_terms():
    candidate = RuleMiner().mine(
        prepare_training_data(_holdout_history(), mode=MiningMode.OPERATIONAL),
        PERMISSIVE,
    )
    subjects = candidate.result_for(PatternStream.TEACHER_SUBJECT)

    assert {rule.subject_code for rule in subjects.rules} == {"S1", "S9"}
    assert subjects.training_term_count == 6


def test_streams_report_why_they_produced_nothing():
    # Only 2018-2021 history: eligible for teacher-subject, invisible to the
    # 2023-onward streams, and the complete 2020-2021 year is the holdout.
    history = (
        term(1, "2018-2019", 1, offering(1)),
        term(2, "2019-2020", 1, offering(2)),
        term(3, "2020-2021", 1, offering(3)),
        term(4, "2020-2021", 2, offering(4)),
        term(5, "2020-2021", 3, offering(5)),
    )

    candidate = RuleMiner().mine(prepare_training_data(history, mode=MiningMode.EVALUATION), PERMISSIVE)

    assert candidate.result_for(PatternStream.TEACHER_SUBJECT).skipped_reason is None
    assert (
        candidate.result_for(PatternStream.TEACHER_DAY).skipped_reason
        is MiningSkipReason.NO_ELIGIBLE_HISTORY
    )


def test_holdout_taking_every_eligible_term_is_reported_separately():
    history = (
        term(1, "2023-2024", 1, offering(1)),
        term(2, "2023-2024", 2, offering(2)),
        term(3, "2024-2025", 1, offering(3)),
    )

    candidate = RuleMiner().mine(prepare_training_data(history, mode=MiningMode.EVALUATION), PERMISSIVE)

    assert (
        candidate.result_for(PatternStream.TEACHER_DAY).skipped_reason
        is MiningSkipReason.HOLDOUT_RESERVED_ALL_TERMS
    )


def test_operational_mining_never_reports_a_holdout_skip():
    history = tuple(
        term(number, "2023-2024", number, offering(number, room_code=None))
        for number in (1, 2, 3)
    )

    candidate = RuleMiner().mine(
        prepare_training_data(history, mode=MiningMode.OPERATIONAL), PERMISSIVE
    )

    assert candidate.result_for(PatternStream.TEACHER_DAY).skipped_reason is None
    assert (
        candidate.result_for(PatternStream.TEACHER_SUBJECT_ROOM).skipped_reason
        is MiningSkipReason.NO_USABLE_TRANSACTIONS
    )


def test_rooms_missing_from_history_leave_that_stream_unmineable():
    history = tuple(
        term(number, year, term_number, offering(number, room_code=None))
        for number, (year, term_number) in enumerate(
            ((year, term_number) for year in ("2023-2024", "2024-2025") for term_number in (1, 2, 3)),
            start=1,
        )
    )

    candidate = RuleMiner().mine(prepare_training_data(history, mode=MiningMode.EVALUATION), PERMISSIVE)
    rooms = candidate.result_for(PatternStream.TEACHER_SUBJECT_ROOM)

    assert rooms.skipped_reason is MiningSkipReason.NO_USABLE_TRANSACTIONS
    assert rooms.rules == ()


def test_mining_is_reproducible_and_ordered():
    history = (
        term(
            1,
            "2023-2024",
            1,
            offering(1, subject_code="S2"),
            offering(2, faculty_id=FACULTY_B, subject_code="S1"),
        ),
        term(
            2,
            "2023-2024",
            2,
            offering(3, subject_code="S2"),
            offering(4, faculty_id=FACULTY_B, subject_code="S1"),
        ),
        term(3, "2024-2025", 1, offering(5)),
        term(4, "2024-2025", 2, offering(6)),
        term(5, "2025-2026", 1, offering(7)),
    )
    prepared = prepare_training_data(history, mode=MiningMode.EVALUATION)

    first = RuleMiner().mine(prepared, PERMISSIVE)
    second = RuleMiner().mine(prepared, PERMISSIVE)

    assert first == second
    # Streams keep their declared order; rules are ordered inside each stream.
    assert [result.stream for result in first.streams] == list(PatternStream)
    for result in first.streams:
        keys = [rule.sort_key for rule in result.rules]
        assert keys == sorted(keys)


def test_per_stream_thresholds_are_applied_to_their_own_stream():
    history = (
        term(1, "2023-2024", 1, offering(1)),
        term(2, "2023-2024", 2, offering(2)),
        term(3, "2023-2024", 3, offering(3)),
        term(4, "2024-2025", 1, offering(4)),
        term(5, "2024-2025", 2, offering(5)),
        term(6, "2024-2025", 3, offering(6)),
    )
    blocking = MiningThresholds(
        min_support=Decimal("0.2"),
        min_confidence=Decimal("0.5"),
        min_lift=Decimal("0"),
        min_term_recurrence=99,
    )

    candidate = RuleMiner().mine(
        prepare_training_data(history, mode=MiningMode.EVALUATION),
        {
            PatternStream.TEACHER_SUBJECT: PERMISSIVE,
            PatternStream.TEACHER_DAY: blocking,
            PatternStream.TEACHER_TIME: PERMISSIVE,
            PatternStream.TEACHER_SUBJECT_ROOM: PERMISSIVE,
        },
    )

    assert candidate.result_for(PatternStream.TEACHER_SUBJECT).rules
    assert candidate.result_for(PatternStream.TEACHER_DAY).rules == ()
    assert candidate.result_for(PatternStream.TEACHER_DAY).skipped_reason is None


def test_missing_stream_thresholds_are_rejected():
    history = (
        term(1, "2023-2024", 1, offering(1)),
        term(2, "2023-2024", 2, offering(2)),
        term(3, "2024-2025", 1, offering(3)),
        term(4, "2024-2025", 2, offering(4)),
    )

    with pytest.raises(ValueError, match="no thresholds supplied"):
        RuleMiner().mine(
            prepare_training_data(history, mode=MiningMode.EVALUATION),
            {PatternStream.TEACHER_SUBJECT: PERMISSIVE},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_support", Decimal("0")),
        ("min_support", Decimal("1.5")),
        ("min_confidence", Decimal("0")),
        ("min_confidence", Decimal("1.5")),
        ("min_lift", Decimal("-0.1")),
        ("min_term_recurrence", 0),
    ],
)
def test_out_of_range_thresholds_are_rejected(field, value):
    settings = {
        "min_support": Decimal("0.2"),
        "min_confidence": Decimal("0.5"),
        "min_lift": Decimal("1"),
        "min_term_recurrence": 1,
    } | {field: value}

    with pytest.raises(ValueError):
        MiningThresholds(**settings)


def test_guidance_score_weights_confidence_by_persistence():
    assert guidance_score(Decimal("1.0"), 1, 4) == Decimal("0.2500")
    assert guidance_score(Decimal("0.5"), 4, 4) == Decimal("0.5000")
    assert guidance_score(Decimal("0.8"), 3, 4) == Decimal("0.6000")

    with pytest.raises(ValueError):
        guidance_score(Decimal("1.0"), 5, 4)
    with pytest.raises(ValueError):
        guidance_score(Decimal("1.0"), 0, 0)


def test_mixed_format_teacher_time_history_mines_one_interval_rule():
    rule = only_rule(
        RuleMiner().mine_stream(
            training_only(
                PatternStream.TEACHER_TIME,
                term(1, "2023-2024", 1, offering(1, time_slot="07:30-09:00")),
                term(2, "2023-2024", 2, offering(2, time_slot="LEC 07:30-09:00")),
            ),
            PERMISSIVE,
        )
    )

    # Both terms count toward the one interval instead of splitting it in two.
    assert rule.time_slot == "07:30-09:00"
    assert rule.term_recurrence == 2
    assert rule.support == Decimal("1")
