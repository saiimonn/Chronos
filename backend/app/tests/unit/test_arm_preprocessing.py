"""CHR-32 / CHR-ARM-001 inclusion, holdout, and transaction tests."""

from dataclasses import replace
from uuid import UUID

import pytest

from app.services.arm import (
    MiningMode,
    HistoricalInclusionPolicy,
    HistoricalOffering,
    HistoricalTerm,
    PatternStream,
    TransactionBuilder,
    prepare_training_data,
    time_comparison_token,
)

FACULTY_ID = UUID("00000000-0000-0000-0000-000000000100")


def offering(
    number: int, *, room_code: str | None = "<ROOM-1>"
) -> HistoricalOffering:
    return HistoricalOffering(
        historical_record_id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
        faculty_id=FACULTY_ID,
        subject_code=f"<SUBJECT-{number}>",
        meeting_days=f"<DAYS-{number}>",
        time_slot=f"<TIME-{number}>",
        room_code=room_code,
    )


def term(
    number: int,
    academic_year: str,
    term_number: int,
    *records: HistoricalOffering,
) -> HistoricalTerm:
    return HistoricalTerm(
        snapshot_id=UUID(f"10000000-0000-0000-0000-{number:012d}"),
        academic_year=academic_year,
        term_number=term_number,
        offerings=tuple(records) or (offering(number),),
    )


def periods(terms: tuple[HistoricalTerm, ...]) -> tuple[tuple[str, int], ...]:
    return tuple((item.academic_year, item.term_number) for item in terms)


def test_policy_holds_out_the_latest_complete_academic_year():
    history = (
        term(9, "2024-2025", 1),
        term(1, "2017-2018", 2),
        term(6, "2023-2024", 2),
        term(4, "2022-2023", 2),
        term(7, "2023-2024", 3),
        term(3, "2022-2023", 1),
        term(2, "2018-2019", 1),
        term(5, "2022-2023", 3),
        term(8, "2023-2024", 1),
    )
    policy = HistoricalInclusionPolicy()

    subject = policy.partition(history, PatternStream.TEACHER_SUBJECT)
    day = policy.partition(history, PatternStream.TEACHER_DAY)

    assert periods(subject.training_terms) == (
        ("2018-2019", 1),
        ("2022-2023", 1),
        ("2022-2023", 2),
    )
    assert periods(subject.holdout_terms) == (
        ("2023-2024", 1),
        ("2023-2024", 2),
        ("2023-2024", 3),
    )
    # A newer, incomplete year is reported, never used as or before the holdout.
    assert periods(subject.unplaced_terms) == (("2024-2025", 1),)
    assert day.training_terms == ()
    assert periods(day.holdout_terms) == periods(subject.holdout_terms)
    assert periods(day.unplaced_terms) == (("2024-2025", 1),)


def test_policy_without_a_complete_academic_year_places_no_term():
    history = (
        term(1, "2023-2024", 1),
        term(2, "2023-2024", 2),
        term(3, "2024-2025", 1),
        term(4, "2024-2025", 3),
    )

    partition = HistoricalInclusionPolicy().partition(
        history, PatternStream.TEACHER_SUBJECT
    )

    assert partition.training_terms == ()
    assert partition.holdout_terms == ()
    assert periods(partition.unplaced_terms) == periods(history)


@pytest.mark.parametrize(
    ("stream", "ineligible_year", "eligible_year"),
    [
        (PatternStream.TEACHER_SUBJECT, "2017-2018", "2018-2019"),
        (PatternStream.TEACHER_DAY, "2022-2023", "2023-2024"),
        (PatternStream.TEACHER_TIME, "2022-2023", "2023-2024"),
        (PatternStream.TEACHER_SUBJECT_ROOM, "2022-2023", "2023-2024"),
    ],
)
def test_policy_enforces_each_stream_year_boundary(
    stream: PatternStream,
    ineligible_year: str,
    eligible_year: str,
):
    policy = HistoricalInclusionPolicy()

    assert not policy.is_eligible(stream, ineligible_year)
    assert policy.is_eligible(stream, eligible_year)


def test_prepare_training_data_never_leaks_rows_from_holdout_terms():
    training = (
        term(1, "2023-2024", 1, offering(1), offering(2)),
        term(5, "2023-2024", 2, offering(7)),
        term(6, "2023-2024", 3, offering(8)),
    )
    holdouts = (
        term(2, "2024-2025", 1, offering(3), offering(4)),
        term(3, "2024-2025", 2, offering(5)),
        term(4, "2024-2025", 3, offering(6)),
    )
    newer = term(10, "2025-2026", 1, offering(10))

    prepared = prepare_training_data(
        (holdouts[2], newer, training[1], *holdouts[:2], training[0], training[2]),
        mode=MiningMode.EVALUATION,
    )

    assert tuple(item.stream for item in prepared) == tuple(PatternStream)
    for stream_data in prepared:
        assert stream_data.partition.training_terms == training
        assert stream_data.partition.holdout_terms == holdouts
        assert stream_data.partition.unplaced_terms == (newer,)
        assert stream_data.mined_terms == training
        assert {
            transaction.historical_record_id
            for transaction in stream_data.transactions
        } == {offering(number).historical_record_id for number in (1, 2, 7, 8)}


def test_operational_mining_uses_every_eligible_term_including_the_holdout():
    history = (
        term(1, "2022-2023", 1, offering(1)),
        term(2, "2023-2024", 1, offering(2)),
        term(3, "2023-2024", 2, offering(3)),
        term(4, "2023-2024", 3, offering(4)),
        term(5, "2024-2025", 1, offering(5)),
    )

    prepared = {
        item.stream: item
        for item in prepare_training_data(history, mode=MiningMode.OPERATIONAL)
    }

    subject = prepared[PatternStream.TEACHER_SUBJECT]
    assert subject.mined_terms == history
    assert len(subject.transactions) == 5
    # Teacher-day starts in 2023, so the 2022 term stays out in both modes.
    day = prepared[PatternStream.TEACHER_DAY]
    assert day.mined_terms == history[1:]
    assert {item.historical_record_id for item in day.transactions} == {
        offering(number).historical_record_id for number in (2, 3, 4, 5)
    }


def test_prepared_transactions_are_reproducible_for_unsorted_inputs():
    # Numbered past the terms below, whose default offerings share their number.
    first_record = offering(11)
    second_record = offering(12)
    first_term = term(1, "2018-2019", 1, second_record, first_record)
    later_terms = (
        term(2, "2019-2020", 1),
        term(3, "2019-2020", 2),
        term(4, "2019-2020", 3),
    )

    first = prepare_training_data((later_terms[2], first_term, *later_terms[:2]), mode=MiningMode.EVALUATION)
    second = prepare_training_data((first_term, *later_terms), mode=MiningMode.EVALUATION)

    assert first == second
    subject_data = first[0]
    assert tuple(
        transaction.historical_record_id
        for transaction in subject_data.transactions
    ) == (first_record.historical_record_id, second_record.historical_record_id)


def test_builder_orders_terms_chronologically_when_used_directly():
    earlier = term(1, "2023-2024", 1)
    later = term(2, "2023-2024", 2)

    transactions = TransactionBuilder().build(
        (later, earlier), PatternStream.TEACHER_SUBJECT
    )

    assert tuple(item.snapshot_id for item in transactions) == (
        earlier.snapshot_id,
        later.snapshot_id,
    )


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (
            PatternStream.TEACHER_SUBJECT,
            (f"faculty={FACULTY_ID}", "subject=<SUBJECT-1>"),
        ),
        (
            PatternStream.TEACHER_DAY,
            (f"faculty={FACULTY_ID}", "day=<DAYS-1>"),
        ),
        (
            PatternStream.TEACHER_TIME,
            (f"faculty={FACULTY_ID}", "time=<TIME-1>"),
        ),
        (
            PatternStream.TEACHER_SUBJECT_ROOM,
            (
                f"faculty={FACULTY_ID}",
                "subject=<SUBJECT-1>",
                "room=<ROOM-1>",
            ),
        ),
    ],
)
def test_builder_uses_fixed_stream_shapes(
    stream: PatternStream, expected: tuple[str, ...]
):
    historical_term = term(1, "2023-2024", 1)

    transactions = TransactionBuilder().build((historical_term,), stream)

    assert len(transactions) == 1
    assert transactions[0].items == expected


def test_subject_room_stream_omits_offerings_without_rooms_only():
    historical_term = term(
        1,
        "2023-2024",
        1,
        offering(1, room_code=None),
        offering(2),
    )
    builder = TransactionBuilder()

    room_transactions = builder.build(
        (historical_term,), PatternStream.TEACHER_SUBJECT_ROOM
    )
    subject_transactions = builder.build(
        (historical_term,), PatternStream.TEACHER_SUBJECT
    )

    assert tuple(item.historical_record_id for item in room_transactions) == (
        offering(2).historical_record_id,
    )
    assert len(subject_transactions) == 2


def test_teacher_subject_only_offerings_reach_only_the_subject_stream():
    """2018-2022 history keeps teacher and subject only (FR-ING-002/003)."""
    subject_only = replace(
        offering(1, room_code=None), meeting_days=None, time_slot=None
    )
    historical_term = term(1, "2019-2020", 1, subject_only)
    builder = TransactionBuilder()

    assert [
        len(builder.build((historical_term,), stream)) for stream in PatternStream
    ] == [1, 0, 0, 0]


def test_prepare_rejects_a_record_shared_by_a_training_and_a_holdout_term():
    """The same record on both sides of the split leaks a reserved offering."""
    shared = offering(9)
    history = (
        term(1, "2023-2024", 1, shared),
        term(2, "2023-2024", 2, offering(2)),
        term(3, "2024-2025", 1, offering(3)),
        term(4, "2024-2025", 2, shared),
    )

    with pytest.raises(ValueError, match="duplicate historical record id"):
        prepare_training_data(history, mode=MiningMode.EVALUATION)


def test_partition_reports_when_the_holdout_reserved_every_eligible_term():
    history = (
        term(1, "2023-2024", 1),
        term(2, "2023-2024", 2),
        term(3, "2024-2025", 1),
    )

    prepared = prepare_training_data(history, mode=MiningMode.EVALUATION)

    for stream_data in prepared:
        partition = stream_data.partition
        assert stream_data.transactions == ()
        assert stream_data.is_trainable is False
        assert partition.has_training_terms is False
        assert partition.eligible_term_count == 3
        assert partition.holdout_reserved_all_eligible_terms is True


def test_partition_separates_absent_history_from_an_exhausted_holdout():
    """A stream whose window matches nothing is not the same as one mined dry."""
    history = (
        term(1, "2018-2019", 1),
        term(2, "2019-2020", 1),
        term(3, "2019-2020", 2),
        term(4, "2019-2020", 3),
    )

    prepared = {item.stream: item for item in prepare_training_data(history, mode=MiningMode.EVALUATION)}

    subject = prepared[PatternStream.TEACHER_SUBJECT].partition
    assert subject.has_training_terms is True
    # The 2019-2020 summer predates the current curriculum and is left out.
    assert subject.eligible_term_count == 3
    assert subject.holdout_reserved_all_eligible_terms is False

    day = prepared[PatternStream.TEACHER_DAY].partition
    assert prepared[PatternStream.TEACHER_DAY].is_trainable is False
    assert day.eligible_term_count == 0
    assert day.holdout_reserved_all_eligible_terms is False


def test_prepared_streams_with_training_terms_are_trainable():
    history = tuple(
        term(number, year, term_number)
        for number, (year, term_number) in enumerate(
            (
                (year, term_number)
                for year in ("2023-2024", "2024-2025")
                for term_number in (1, 2, 3)
            ),
            start=1,
        )
    )

    for stream_data in prepare_training_data(history, mode=MiningMode.EVALUATION):
        assert stream_data.is_trainable is True
        assert stream_data.partition.has_training_terms is True
        assert stream_data.partition.eligible_term_count == 6


def test_builder_rejects_duplicate_historical_record_ids():
    duplicate = offering(1)
    history = (
        term(1, "2023-2024", 1, duplicate),
        term(2, "2023-2024", 2, duplicate),
    )

    with pytest.raises(ValueError, match="duplicate historical record id"):
        TransactionBuilder().build(history, PatternStream.TEACHER_SUBJECT)


def test_offering_accepts_an_absent_room():
    assert offering(1, room_code=None).room_code is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_code", " "),
        ("meeting_days", ""),
        ("time_slot", "\t"),
        ("room_code", " "),
    ],
)
def test_offering_rejects_blank_transaction_values(field: str, value: str):
    with pytest.raises(ValueError, match=field):
        replace(offering(1), **{field: value})


@pytest.mark.parametrize("academic_year", ["2023", "2023/2024", "2023-2025"])
def test_policy_rejects_ambiguous_academic_years(academic_year: str):
    with pytest.raises(ValueError, match="academic year"):
        HistoricalInclusionPolicy().partition(
            (term(1, academic_year, 1),), PatternStream.TEACHER_SUBJECT
        )


def test_policy_rejects_duplicate_term_periods():
    history = (
        term(1, "2023-2024", 1),
        term(2, "2023-2024", 1),
    )

    with pytest.raises(ValueError, match="duplicate historical term period"):
        HistoricalInclusionPolicy().partition(history, PatternStream.TEACHER_DAY)


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("07:30-09:00", "07:30-09:00"),
        ("LEC 07:30-09:00", "07:30-09:00"),
        ("LAB 07:30-10:00", "07:30-10:00"),
        ("LAB 18:30-21:00", "18:30-21:00"),
    ],
)
def test_recognized_time_labels_compare_as_their_bare_interval(label: str, token: str):
    assert time_comparison_token(label) == token


@pytest.mark.parametrize(
    "label",
    [
        "<TIME-1>",
        "lec 07:30-09:00",
        "LEC07:30-09:00",
        "LEC  07:30-09:00",
        "TUT 07:30-09:00",
        " LEC 07:30-09:00",
        "LEC 07:30-09:00 ",
        "7:30-9:00",
        "07:30 - 09:00",
        "07:30-07:30",
        "09:00-07:30",
        "24:00-25:00",
        "07:60-09:00",
        "LEC 07:30",
        "LEC 07:30-09:00\n",
        "07:30-09:00\n",
        "07:30-09:00\r\n",
    ],
)
def test_unrecognized_time_labels_are_left_exactly_as_stored(label: str):
    assert time_comparison_token(label) == label


def _time_offering(number: int, time_slot: str) -> HistoricalOffering:
    return replace(offering(number), time_slot=time_slot)


def test_teacher_time_items_match_across_bare_and_prefixed_labels():
    history = term(
        1,
        "2023-2024",
        1,
        _time_offering(1, "07:30-09:00"),
        _time_offering(2, "LEC 07:30-09:00"),
        _time_offering(3, "LEC 07:30-10:00"),
        _time_offering(4, "lec 07:30-09:00"),
    )

    transactions = TransactionBuilder().build((history,), PatternStream.TEACHER_TIME)

    assert [item.items[1] for item in transactions] == [
        "time=07:30-09:00",
        "time=07:30-09:00",
        # A different end time is a different interval.
        "time=07:30-10:00",
        # Unrecognized text gains no match.
        "time=lec 07:30-09:00",
    ]
    # The stored offerings are untouched.
    assert [o.time_slot for o in history.offerings] == [
        "07:30-09:00",
        "LEC 07:30-09:00",
        "LEC 07:30-10:00",
        "lec 07:30-09:00",
    ]


def test_mixed_format_teacher_time_transactions_are_repeatable():
    history = (
        term(1, "2023-2024", 1, _time_offering(2, "LAB 07:30-10:00"), _time_offering(1, "07:30-10:00")),
        term(2, "2023-2024", 2, _time_offering(3, "LEC 13:00-14:30")),
    )

    first = TransactionBuilder().build(history, PatternStream.TEACHER_TIME)
    second = TransactionBuilder().build(tuple(reversed(history)), PatternStream.TEACHER_TIME)

    assert first == second


def test_time_normalization_does_not_reach_the_other_streams():
    history = term(
        1,
        "2023-2024",
        1,
        replace(
            offering(1),
            subject_code="LEC 07:30-09:00",
            meeting_days="LEC 07:30-09:00",
            room_code="LEC 07:30-09:00",
        ),
    )
    builder = TransactionBuilder()

    assert builder.build((history,), PatternStream.TEACHER_SUBJECT)[0].items[1] == "subject=LEC 07:30-09:00"
    assert builder.build((history,), PatternStream.TEACHER_DAY)[0].items[1] == "day=LEC 07:30-09:00"
    assert builder.build((history,), PatternStream.TEACHER_SUBJECT_ROOM)[0].items[1:] == (
        "subject=LEC 07:30-09:00",
        "room=LEC 07:30-09:00",
    )


def test_summer_terms_before_the_current_curriculum_are_not_eligible():
    from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream

    policy = HistoricalInclusionPolicy()
    stream = PatternStream.TEACHER_SUBJECT
    assert not policy.is_term_eligible(stream, "2022-2023", 3)
    assert policy.is_term_eligible(stream, "2022-2023", 2)
    assert policy.is_term_eligible(stream, "2023-2024", 3)
