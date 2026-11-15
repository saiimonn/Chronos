import re
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID


class MiningMode(StrEnum):
    """Which eligible terms a mining run may draw on (thesis §4.3, PDF p.49-52).

    The chronological holdout is withheld only during the empirical study.
    Operational rule sets, mined after the holdout validation is recorded, use
    every eligible term.
    """

    OPERATIONAL = "operational"
    EVALUATION = "evaluation"


class PatternStream(StrEnum):
    TEACHER_SUBJECT = "teacher_subject"
    TEACHER_DAY = "teacher_day"
    TEACHER_TIME = "teacher_time"
    TEACHER_SUBJECT_ROOM = "teacher_subject_room"


# Bootstrap and captured history store a bare "07:30-09:00"; operational slots
# are labelled "LEC 07:30-09:00" / "LAB 07:30-10:00". Only these two shapes of a
# complete 24-hour interval are recognized.
_TIME_LABEL = re.compile(
    r"(?:(?:LEC|LAB) )?((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d)"
)


def time_comparison_token(label: str) -> str:
    """The token teacher-time items and guidance lookups compare on.

    A recognized label becomes its bare interval, so the same complete interval
    matches whichever format recorded it, while a different end time stays a
    different token. Anything unrecognized is returned unchanged and can only
    match itself exactly. Stored values are never rewritten; this is applied at
    comparison boundaries only.
    """
    match = _TIME_LABEL.fullmatch(label)
    if match is None or match.group(2) <= match.group(1):
        return label
    return f"{match.group(1)}-{match.group(2)}"


@dataclass(frozen=True, slots=True)
class HistoricalOffering:
    historical_record_id: UUID
    faculty_id: UUID
    subject_code: str
    # Absent for teacher-subject-only (2018-2022) history.
    meeting_days: str | None
    time_slot: str | None
    room_code: str | None = None

    def __post_init__(self) -> None:
        if not self.subject_code.strip():
            raise ValueError("subject_code must not be blank")
        for field in ("meeting_days", "time_slot", "room_code"):
            value = getattr(self, field)
            if value is not None and not value.strip():
                raise ValueError(f"{field} must be absent or non-blank")


@dataclass(frozen=True, slots=True)
class HistoricalTerm:
    """One complete, immutable finalized-term snapshot and all its offerings."""

    snapshot_id: UUID
    academic_year: str
    term_number: int
    offerings: tuple[HistoricalOffering, ...]

    def __post_init__(self) -> None:
        if self.term_number < 1:
            raise ValueError("term_number must be positive")


@dataclass(frozen=True, slots=True)
class HistoricalPartition:
    training_terms: tuple[HistoricalTerm, ...]
    holdout_terms: tuple[HistoricalTerm, ...]
    # Terms of academic years newer than the holdout year, or every eligible
    # term when no academic year is complete. Evaluation cannot place them
    # without leaking later history into training, so it reports and skips
    # them; operational mining uses them.
    unplaced_terms: tuple[HistoricalTerm, ...] = ()

    @property
    def eligible_terms(self) -> tuple[HistoricalTerm, ...]:
        return self.training_terms + self.holdout_terms + self.unplaced_terms

    @property
    def eligible_term_count(self) -> int:
        return len(self.eligible_terms)

    @property
    def has_training_terms(self) -> bool:
        return bool(self.training_terms)

    @property
    def holdout_reserved_all_eligible_terms(self) -> bool:
        """True when eligible history exists but the holdout left none to train on.

        A stream whose window matches no history at all is expected early in the
        dataset's life and is reported by `eligible_term_count` being zero. This
        property marks the different, surprising case in evaluation mining:
        eligible terms were found, but the holdout year and any unplaced newer
        terms claimed all of them. Callers must distinguish the two rather than
        reading an empty transaction tuple as a successful run.
        """
        return not self.training_terms and bool(self.eligible_terms)


@dataclass(frozen=True, slots=True)
class ARMTransaction:
    snapshot_id: UUID
    historical_record_id: UUID
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StreamTrainingData:
    stream: PatternStream
    partition: HistoricalPartition
    transactions: tuple[ARMTransaction, ...]
    mode: MiningMode = MiningMode.EVALUATION

    @property
    def mined_terms(self) -> tuple[HistoricalTerm, ...]:
        """The terms this stream's transactions were built from."""
        if self.mode is MiningMode.OPERATIONAL:
            return self.partition.eligible_terms
        return self.partition.training_terms

    @property
    def is_trainable(self) -> bool:
        """True when this stream has transactions Apriori can actually mine."""
        return bool(self.transactions)


class HistoricalInclusionPolicy:
    """CHR-32 / CHR-ARM-001 stream eligibility and temporal holdout policy."""

    # The holdout is the latest complete academic year: terms 1 to 3 of the
    # trimestral calendar (thesis §4.3, PDF p.49).
    HOLDOUT_TERM_COUNT = 3
    _ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{4})$")
    _EARLIEST_START_YEAR = {
        PatternStream.TEACHER_SUBJECT: 2018,
        PatternStream.TEACHER_DAY: 2023,
        PatternStream.TEACHER_TIME: 2023,
        PatternStream.TEACHER_SUBJECT_ROOM: 2023,
    }

    def is_eligible(self, stream: PatternStream, academic_year: str) -> bool:
        return self._start_year(academic_year) >= self._EARLIEST_START_YEAR[stream]

    @classmethod
    def is_parsable_academic_year(cls, academic_year: str) -> bool:
        """Whether this policy can read the year at all.

        Callers that must report on a stored year rather than fail on it ask this
        first, so an unreadable year is a fact about one term instead of an error
        that takes down the whole operation.
        """
        try:
            cls._start_year(academic_year)
        except ValueError:
            return False
        return True

    def earliest_academic_year(self, stream: PatternStream) -> str:
        """The oldest academic year this stream may mine, e.g. "2018-2019".

        A rule set records the window each stream was allowed to draw on, so the
        year is published here rather than re-derived by whatever writes the
        threshold row.
        """
        start_year = self._EARLIEST_START_YEAR[stream]
        return f"{start_year}-{start_year + 1}"

    def partition(
        self,
        terms: tuple[HistoricalTerm, ...],
        stream: PatternStream,
    ) -> HistoricalPartition:
        eligible = tuple(
            sorted(
                (
                    term
                    for term in terms
                    if self.is_eligible(stream, term.academic_year)
                ),
                key=self._term_sort_key,
            )
        )
        self._reject_duplicate_periods(eligible)

        term_numbers: dict[str, set[int]] = {}
        for term in eligible:
            term_numbers.setdefault(term.academic_year, set()).add(term.term_number)
        required = set(range(1, self.HOLDOUT_TERM_COUNT + 1))
        complete_years = [
            year for year, numbers in term_numbers.items() if required <= numbers
        ]
        if not complete_years:
            return HistoricalPartition(
                training_terms=(), holdout_terms=(), unplaced_terms=eligible
            )
        holdout_start_year = max(self._start_year(year) for year in complete_years)

        def years_after_holdout(term: HistoricalTerm) -> int:
            return self._start_year(term.academic_year) - holdout_start_year

        return HistoricalPartition(
            training_terms=tuple(term for term in eligible if years_after_holdout(term) < 0),
            holdout_terms=tuple(term for term in eligible if years_after_holdout(term) == 0),
            unplaced_terms=tuple(term for term in eligible if years_after_holdout(term) > 0),
        )

    def _term_sort_key(self, term: HistoricalTerm) -> tuple[int, int, str]:
        return (
            self._start_year(term.academic_year),
            term.term_number,
            str(term.snapshot_id),
        )

    @classmethod
    def _start_year(cls, academic_year: str) -> int:
        match = cls._ACADEMIC_YEAR.fullmatch(academic_year)
        if match is None:
            raise ValueError(
                f"academic year must use YYYY-YYYY format: {academic_year!r}"
            )
        start_year, end_year = (int(part) for part in match.groups())
        if end_year != start_year + 1:
            raise ValueError(
                f"academic year must cover consecutive years: {academic_year!r}"
            )
        return start_year

    @staticmethod
    def _reject_duplicate_periods(terms: tuple[HistoricalTerm, ...]) -> None:
        periods: set[tuple[str, int]] = set()
        for term in terms:
            period = (term.academic_year, term.term_number)
            if period in periods:
                raise ValueError(f"duplicate historical term period: {period!r}")
            periods.add(period)


def reject_duplicate_record_ids(terms: tuple[HistoricalTerm, ...]) -> None:
    """Reject a historical record that appears in more than one term.

    A repeated record id means the same past offering was captured twice. Left
    alone it inflates support for a pattern that occurred once, and when the two
    captures straddle the holdout boundary it leaks a reserved offering into
    training. Both are checked here, over the full input, because a check that
    ran only over the training partition could not see the second case.
    """
    record_ids: set[UUID] = set()
    for term in terms:
        for offering in term.offerings:
            if offering.historical_record_id in record_ids:
                raise ValueError(
                    f"duplicate historical record id: {offering.historical_record_id}"
                )
            record_ids.add(offering.historical_record_id)


class TransactionBuilder:
    """Builds one deterministic Apriori basket per historical offering."""

    def build(
        self,
        terms: tuple[HistoricalTerm, ...],
        stream: PatternStream,
    ) -> tuple[ARMTransaction, ...]:
        reject_duplicate_record_ids(terms)
        transactions: list[ARMTransaction] = []
        for term in sorted(
            terms,
            key=lambda item: (
                HistoricalInclusionPolicy._start_year(item.academic_year),
                item.term_number,
                str(item.snapshot_id),
            ),
        ):
            for offering in sorted(
                term.offerings, key=lambda item: str(item.historical_record_id)
            ):
                items = self._items(offering, stream)
                if items is None:
                    continue
                transactions.append(
                    ARMTransaction(
                        snapshot_id=term.snapshot_id,
                        historical_record_id=offering.historical_record_id,
                        items=items,
                    )
                )
        return tuple(transactions)

    @staticmethod
    def _items(
        offering: HistoricalOffering,
        stream: PatternStream,
    ) -> tuple[str, ...] | None:
        faculty = f"faculty={offering.faculty_id}"
        if stream is PatternStream.TEACHER_SUBJECT:
            return faculty, f"subject={offering.subject_code}"
        if stream is PatternStream.TEACHER_DAY:
            if offering.meeting_days is None:
                return None
            return faculty, f"day={offering.meeting_days}"
        if stream is PatternStream.TEACHER_TIME:
            if offering.time_slot is None:
                return None
            return faculty, f"time={time_comparison_token(offering.time_slot)}"
        if stream is PatternStream.TEACHER_SUBJECT_ROOM:
            if offering.room_code is None:
                return None
            return (
                faculty,
                f"subject={offering.subject_code}",
                f"room={offering.room_code}",
            )
        raise ValueError(f"unsupported pattern stream: {stream!r}")


def prepare_training_data(
    terms: tuple[HistoricalTerm, ...],
    policy: HistoricalInclusionPolicy | None = None,
    *,
    mode: MiningMode,
) -> tuple[StreamTrainingData, ...]:
    """Build each stream's transactions from the terms ``mode`` may mine.

    The mode has no default: whether the holdout is withheld is a decision
    every caller states.
    """
    reject_duplicate_record_ids(terms)
    inclusion_policy = policy or HistoricalInclusionPolicy()
    builder = TransactionBuilder()
    prepared: list[StreamTrainingData] = []
    for stream in PatternStream:
        partition = inclusion_policy.partition(terms, stream)
        data = StreamTrainingData(
            stream=stream, partition=partition, transactions=(), mode=mode
        )
        prepared.append(
            replace(data, transactions=builder.build(data.mined_terms, stream))
        )
    return tuple(prepared)
