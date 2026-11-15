"""CHR-33 / CHR-ARM-002 Apriori mining, filtering, recurrence, and scoring.

Mining runs on the training partition CHR-ARM-001 produced. The holdout terms
never reach this module, so every metric and every threshold decision here is
derived from training terms only.

The stream shapes follow the thesis: teacher-subject, teacher-day, and
teacher-time are mined as `faculty -> consequent`, while teacher-subject-room is
mined as `faculty + subject -> room`. The last one is deliberately not
`faculty -> subject + room`: the question the department actually asks is which
room an instructor uses *for that subject*, and only the two-item antecedent
puts that conditional in the confidence denominator.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules

from app.services.arm.preprocessing import (
    ARMTransaction,
    MiningMode,
    PatternStream,
    StreamTrainingData,
)

# Metrics are quantized to the scale the `extracted_rule` columns declare, so a
# mined rule and its persisted row carry the same number rather than two
# renderings of one float.
_METRIC_SCALE = Decimal("0.0001")

_ITEM_SEPARATOR = "="

# Antecedent and consequent item kinds per stream. `TransactionBuilder` emits
# these kinds as the `kind=value` prefixes of every item.
_STREAM_SHAPE: dict[PatternStream, tuple[frozenset[str], frozenset[str]]] = {
    PatternStream.TEACHER_SUBJECT: (frozenset({"faculty"}), frozenset({"subject"})),
    PatternStream.TEACHER_DAY: (frozenset({"faculty"}), frozenset({"day"})),
    PatternStream.TEACHER_TIME: (frozenset({"faculty"}), frozenset({"time"})),
    PatternStream.TEACHER_SUBJECT_ROOM: (
        frozenset({"faculty", "subject"}),
        frozenset({"room"}),
    ),
}


class MiningSkipReason(StrEnum):
    """Why a stream produced no rules for a reason other than filtering.

    An empty rule tuple is ambiguous on its own — it could mean the thresholds
    rejected everything, or that nothing was mineable in the first place. The
    summary the administrator reviews has to tell those apart, so a skipped
    stream carries its reason instead of an empty result.
    """

    NO_ELIGIBLE_HISTORY = "no_eligible_history"
    HOLDOUT_RESERVED_ALL_TERMS = "holdout_reserved_all_terms"
    NO_USABLE_TRANSACTIONS = "no_usable_transactions"


@dataclass(frozen=True, slots=True)
class MiningThresholds:
    """The filters one mining run applies to one stream.

    There are no defaults. The thesis selects these from the training terms and
    the practical availability of rules in a small dataset, so a constant here
    would be an invented institutional value; the caller supplies them and the
    run records what it used.
    """

    min_support: Decimal
    min_confidence: Decimal
    min_lift: Decimal
    min_term_recurrence: int

    def __post_init__(self) -> None:
        if not Decimal(0) < self.min_support <= Decimal(1):
            raise ValueError("min_support must fall in (0, 1]")
        if not Decimal(0) < self.min_confidence <= Decimal(1):
            raise ValueError("min_confidence must fall in (0, 1]")
        if self.min_lift < Decimal(0):
            raise ValueError("min_lift must not be negative")
        if self.min_term_recurrence < 1:
            raise ValueError("min_term_recurrence must be positive")


@dataclass(frozen=True, slots=True)
class MinedRule:
    """One retained association rule and the metrics it was retained on."""

    stream: PatternStream
    faculty_id: UUID
    subject_code: str | None
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int
    guidance_score: Decimal

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            str(self.stream),
            str(self.faculty_id),
            self.subject_code or "",
            self.room_code or "",
            self.meeting_days or "",
            self.time_slot or "",
        )


@dataclass(frozen=True, slots=True)
class StreamMiningResult:
    stream: PatternStream
    thresholds: MiningThresholds
    training_term_count: int
    transaction_count: int
    rules: tuple[MinedRule, ...]
    skipped_reason: MiningSkipReason | None = None


@dataclass(frozen=True, slots=True)
class CandidateRuleSet:
    """The four-stream output of one mining run, before it is persisted."""

    streams: tuple[StreamMiningResult, ...]

    @property
    def rules(self) -> tuple[MinedRule, ...]:
        return tuple(rule for stream in self.streams for rule in stream.rules)

    @property
    def is_empty(self) -> bool:
        return not self.rules

    def result_for(self, stream: PatternStream) -> StreamMiningResult:
        for result in self.streams:
            if result.stream is stream:
                return result
        raise KeyError(f"stream was not mined: {stream!r}")


class RuleMiner:
    """Runs Mlxtend Apriori over prepared transactions and scores what survives."""

    def mine(
        self,
        prepared: Sequence[StreamTrainingData],
        thresholds: MiningThresholds | Mapping[PatternStream, MiningThresholds],
    ) -> CandidateRuleSet:
        return CandidateRuleSet(
            streams=tuple(
                self.mine_stream(data, self._thresholds_for(thresholds, data.stream))
                for data in prepared
            )
        )

    def mine_stream(
        self,
        data: StreamTrainingData,
        thresholds: MiningThresholds,
    ) -> StreamMiningResult:
        term_count = len({item.snapshot_id for item in data.transactions})
        skipped = self._skip_reason(data)
        if skipped is not None:
            return StreamMiningResult(
                stream=data.stream,
                thresholds=thresholds,
                training_term_count=term_count,
                transaction_count=len(data.transactions),
                rules=(),
                skipped_reason=skipped,
            )

        rules = self._mine_rules(data, thresholds, term_count)
        return StreamMiningResult(
            stream=data.stream,
            thresholds=thresholds,
            training_term_count=term_count,
            transaction_count=len(data.transactions),
            rules=rules,
        )

    def _mine_rules(
        self,
        data: StreamTrainingData,
        thresholds: MiningThresholds,
        term_count: int,
    ) -> tuple[MinedRule, ...]:
        frame = self._one_hot(data.transactions)
        frequent = apriori(
            frame, min_support=float(thresholds.min_support), use_colnames=True
        )
        if frequent.empty:
            return ()

        candidates = association_rules(
            frequent,
            num_itemsets=len(frame),
            metric="confidence",
            min_threshold=float(thresholds.min_confidence),
        )
        antecedent_kinds, consequent_kinds = _STREAM_SHAPE[data.stream]

        mined: list[MinedRule] = []
        for row in candidates.itertuples(index=False):
            antecedents = self._parse_items(row.antecedents)
            consequents = self._parse_items(row.consequents)
            if antecedents is None or consequents is None:
                continue
            if set(antecedents) != antecedent_kinds:
                continue
            if set(consequents) != consequent_kinds:
                continue

            lift = _quantize(row.lift)
            if lift < thresholds.min_lift:
                continue

            items = frozenset(row.antecedents) | frozenset(row.consequents)
            recurrence = self._term_recurrence(data.transactions, items)
            if recurrence < thresholds.min_term_recurrence:
                continue

            confidence = _quantize(row.confidence)
            mined.append(
                self._build_rule(
                    stream=data.stream,
                    fields=antecedents | consequents,
                    support=_quantize(row.support),
                    confidence=confidence,
                    lift=lift,
                    recurrence=recurrence,
                    term_count=term_count,
                )
            )
        return tuple(sorted(mined, key=lambda rule: rule.sort_key))

    @staticmethod
    def _thresholds_for(
        thresholds: MiningThresholds | Mapping[PatternStream, MiningThresholds],
        stream: PatternStream,
    ) -> MiningThresholds:
        if isinstance(thresholds, MiningThresholds):
            return thresholds
        try:
            return thresholds[stream]
        except KeyError:
            raise ValueError(f"no thresholds supplied for stream: {stream!r}") from None

    @staticmethod
    def _skip_reason(data: StreamTrainingData) -> MiningSkipReason | None:
        if data.is_trainable:
            return None
        if (
            data.mode is MiningMode.EVALUATION
            and data.partition.holdout_reserved_all_eligible_terms
        ):
            return MiningSkipReason.HOLDOUT_RESERVED_ALL_TERMS
        if not data.partition.eligible_term_count:
            return MiningSkipReason.NO_ELIGIBLE_HISTORY
        # Training terms exist but none of their offerings formed a basket —
        # teacher-subject-room over records that never recorded a room.
        return MiningSkipReason.NO_USABLE_TRANSACTIONS

    @staticmethod
    def _one_hot(transactions: Sequence[ARMTransaction]) -> pd.DataFrame:
        columns = sorted(
            {item for transaction in transactions for item in transaction.items}
        )
        index = {column: position for position, column in enumerate(columns)}
        rows = [[False] * len(columns) for _ in transactions]
        for row, transaction in zip(rows, transactions, strict=True):
            for item in transaction.items:
                row[index[item]] = True
        return pd.DataFrame(rows, columns=columns, dtype=bool)

    @staticmethod
    def _term_recurrence(
        transactions: Sequence[ARMTransaction],
        items: frozenset[str],
    ) -> int:
        """Distinct training terms in which the whole pattern was observed.

        Counted over terms rather than over transactions: a rule that appears
        twenty times inside one term is one term's habit, and the thesis filters
        on persistence across terms.
        """
        return len(
            {
                transaction.snapshot_id
                for transaction in transactions
                if items <= frozenset(transaction.items)
            }
        )

    @staticmethod
    def _parse_items(items: frozenset[str]) -> dict[str, str] | None:
        parsed: dict[str, str] = {}
        for item in items:
            kind, separator, value = item.partition(_ITEM_SEPARATOR)
            if not separator:
                return None
            if kind in parsed:
                # Two values of one kind cannot describe a single offering, so
                # the itemset is not one of the four stream shapes.
                return None
            parsed[kind] = value
        return parsed

    @staticmethod
    def _build_rule(
        *,
        stream: PatternStream,
        fields: dict[str, str],
        support: Decimal,
        confidence: Decimal,
        lift: Decimal,
        recurrence: int,
        term_count: int,
    ) -> MinedRule:
        return MinedRule(
            stream=stream,
            faculty_id=UUID(fields["faculty"]),
            subject_code=fields.get("subject"),
            room_code=fields.get("room"),
            meeting_days=fields.get("day"),
            time_slot=fields.get("time"),
            support=support,
            confidence=confidence,
            lift=lift,
            term_recurrence=recurrence,
            guidance_score=guidance_score(confidence, recurrence, term_count),
        )


def guidance_score(
    confidence: Decimal,
    term_recurrence: int,
    training_term_count: int,
) -> Decimal:
    """Direct rule score: confidence weighted by how many terms the rule held in.

    `confidence * (term_recurrence / training_term_count)` stays in [0, 1] by
    construction and keeps both factors visible, so a score can be explained to
    the department as "how reliable" times "how persistent" without a fitted
    parameter. The denominator counts the terms that actually contributed
    transactions to this stream, not every training term: a stream whose records
    lack rooms would otherwise cap its own rules below 1 for a reason that has
    nothing to do with the pattern.
    """
    if training_term_count < 1:
        raise ValueError("training_term_count must be positive")
    if term_recurrence > training_term_count:
        raise ValueError("term_recurrence cannot exceed training_term_count")
    persistence = Decimal(term_recurrence) / Decimal(training_term_count)
    return _quantize(confidence * persistence)


def _quantize(value: object) -> Decimal:
    return Decimal(str(value)).quantize(_METRIC_SCALE, rounding=ROUND_HALF_UP)
