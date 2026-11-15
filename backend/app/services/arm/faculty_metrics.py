"""Per-teacher diagnostics: each teacher's best candidate pattern per stream.

Apriori only reports itemsets that already clear `min_support`, so the rules a
version keeps say nothing about the teachers who were left out. This module
counts every candidate pattern directly, for every teacher in a stream's
transactions, and keeps the one that came closest to being retained, together
with which of the four thresholds it met. The Rule Sets page reads the result
to show, per teacher, why a rule exists or does not.

The counts use the same definitions the miner does: support over all of the
stream's transactions, confidence over the antecedent, lift against the
consequent's own frequency, and recurrence over distinct terms. Lift is
compared after quantizing, as `RuleMiner` compares it. A candidate that meets
all four thresholds is therefore exactly a rule `RuleMiner` retains.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from uuid import UUID

from app.services.arm.mining import MiningThresholds
from app.services.arm.preprocessing import ARMTransaction, PatternStream

_METRIC_SCALE = Decimal("0.0001")


def _quantize(value: float) -> Decimal:
    """The same rounding `mining._quantize` applies to every stored metric."""
    return Decimal(str(value)).quantize(_METRIC_SCALE, rounding=ROUND_HALF_UP)

# The consequent kind each stream predicts, and whether the subject is part of
# the antecedent (teacher-subject-room is mined as faculty + subject -> room).
_CONSEQUENT_KIND = {
    PatternStream.TEACHER_SUBJECT: "subject",
    PatternStream.TEACHER_DAY: "day",
    PatternStream.TEACHER_TIME: "time",
    PatternStream.TEACHER_SUBJECT_ROOM: "room",
}


@dataclass(frozen=True, slots=True)
class FacultyStreamMetric:
    """One teacher's best candidate pattern in one stream, retained or not."""

    stream: PatternStream
    faculty_key: str
    subject_code: str | None
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
    # The teacher's offerings in this stream's transactions.
    offering_count: int
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int
    meets_support: bool
    meets_confidence: bool
    meets_lift: bool
    meets_term_recurrence: bool

    @property
    def meets_all(self) -> bool:
        return (
            self.meets_support
            and self.meets_confidence
            and self.meets_lift
            and self.meets_term_recurrence
        )

    @property
    def faculty_id(self) -> UUID:
        return UUID(self.faculty_key)


def _parse(items: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        kind, _, value = item.partition("=")
        parsed[kind] = value
    return parsed


def best_faculty_patterns(
    stream: PatternStream,
    transactions: Iterable[ARMTransaction],
    thresholds: MiningThresholds,
    training_term_count: int,
) -> tuple[FacultyStreamMetric, ...]:
    """Every teacher's closest-to-retained candidate pattern in ``stream``.

    "Closest" ranks first by how many of the four thresholds the pattern meets,
    then (among fully retained patterns) by guidance score, then by support,
    confidence, lift and recurrence; ties fall to the pattern text, so the
    choice is deterministic.
    """
    rows = [(transaction.snapshot_id, _parse(transaction.items)) for transaction in transactions]
    total = len(rows)
    if total == 0:
        return ()
    consequent_kind = _CONSEQUENT_KIND[stream]
    with_subject = stream is PatternStream.TEACHER_SUBJECT_ROOM

    offerings: dict[str, int] = defaultdict(int)
    antecedent_count: dict[tuple[str, ...], int] = defaultdict(int)
    consequent_count: dict[str, int] = defaultdict(int)
    pair_count: dict[tuple[tuple[str, ...], str], int] = defaultdict(int)
    pair_terms: dict[tuple[tuple[str, ...], str], set] = defaultdict(set)
    for snapshot_id, fields in rows:
        faculty = fields["faculty"]
        consequent = fields[consequent_kind]
        antecedent = (faculty, fields["subject"]) if with_subject else (faculty,)
        offerings[faculty] += 1
        antecedent_count[antecedent] += 1
        consequent_count[consequent] += 1
        pair_count[(antecedent, consequent)] += 1
        pair_terms[(antecedent, consequent)].add(snapshot_id)

    min_support = Fraction(str(thresholds.min_support))
    min_confidence = Fraction(str(thresholds.min_confidence))

    best: dict[str, tuple[tuple, str, FacultyStreamMetric]] = {}
    for (antecedent, consequent), count in pair_count.items():
        faculty = antecedent[0]
        support = Fraction(count, total)
        confidence = Fraction(count, antecedent_count[antecedent])
        lift = confidence / Fraction(consequent_count[consequent], total)
        quantized_lift = _quantize(float(lift))
        recurrence = len(pair_terms[(antecedent, consequent)])
        subject = antecedent[1] if with_subject else (
            consequent if consequent_kind == "subject" else None
        )
        metric = FacultyStreamMetric(
            stream=stream,
            faculty_key=faculty,
            subject_code=subject,
            room_code=consequent if consequent_kind == "room" else None,
            meeting_days=consequent if consequent_kind == "day" else None,
            time_slot=consequent if consequent_kind == "time" else None,
            offering_count=offerings[faculty],
            support=_quantize(float(support)),
            confidence=_quantize(float(confidence)),
            lift=quantized_lift,
            term_recurrence=recurrence,
            meets_support=support >= min_support,
            meets_confidence=confidence >= min_confidence,
            meets_lift=quantized_lift >= thresholds.min_lift,
            meets_term_recurrence=recurrence >= thresholds.min_term_recurrence,
        )
        met = sum(
            (
                metric.meets_support,
                metric.meets_confidence,
                metric.meets_lift,
                metric.meets_term_recurrence,
            )
        )
        guidance = (
            confidence * Fraction(recurrence, training_term_count)
            if metric.meets_all
            else Fraction(0)
        )
        pattern = "|".join(antecedent[1:] + (consequent,))
        rank = (met, guidance, support, confidence, lift, recurrence)
        current = best.get(faculty)
        # Higher rank wins; an exact tie goes to the alphabetically first pattern.
        if (
            current is None
            or rank > current[0]
            or (rank == current[0] and pattern < current[1])
        ):
            best[faculty] = (rank, pattern, metric)

    return tuple(best[faculty][2] for faculty in sorted(best))


