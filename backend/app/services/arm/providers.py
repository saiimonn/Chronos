"""CHR-33 / CHR-ARM-002 historical guidance providers.

The GA reads guidance through `HistoricalRuleProvider` and never through the
ORM, so the same fitness code runs against a deterministic fixture set, against
a database rule set, and against a controlled research input.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.extracted_rule import ExtractedRule
from app.models.rule_set import RuleSet
from app.services.arm.mining import MinedRule
from app.services.arm.preprocessing import PatternStream, time_comparison_token

_ConsequentKey = tuple[str | None, str | None, str | None, str | None]
_RuleKey = tuple[PatternStream, UUID, _ConsequentKey]


class NoActiveRuleSetError(LookupError):
    def __init__(self) -> None:
        super().__init__("no active rule set")


class GuidanceKeyCollisionError(ValueError):
    """Two distinct stored rules compare as the same pattern.

    Raised instead of keeping either one: which rule survives would otherwise
    depend on row order, and summing them would invent a score no mining run
    produced.
    """

    def __init__(self, first: "GuidanceRule", second: "GuidanceRule"):
        super().__init__(
            f"guidance rules collide after time normalization: stream={first.stream} "
            f"faculty_id={first.faculty_id} time_slot={first.time_slot!r} "
            f"and time_slot={second.time_slot!r}"
        )
        self.first = first
        self.second = second


def _comparison_key(
    subject_code: str | None,
    room_code: str | None,
    meeting_days: str | None,
    time_slot: str | None,
) -> _ConsequentKey:
    return (
        subject_code,
        room_code,
        meeting_days,
        None if time_slot is None else time_comparison_token(time_slot),
    )


class RuleSetNotFoundError(LookupError):
    def __init__(self, rule_set_id: UUID):
        super().__init__(f"rule set not found: {rule_set_id}")
        self.rule_set_id = rule_set_id


@dataclass(frozen=True, slots=True)
class GuidanceRule:
    """One rule as the GA consumes it: a pattern and its soft reward."""

    stream: PatternStream
    faculty_id: UUID
    subject_code: str | None
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
    guidance_score: Decimal
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int

    @property
    def consequent_key(self) -> _ConsequentKey:
        return (self.subject_code, self.room_code, self.meeting_days, self.time_slot)

    @classmethod
    def from_mined(cls, rule: MinedRule) -> "GuidanceRule":
        return cls(
            stream=rule.stream,
            faculty_id=rule.faculty_id,
            subject_code=rule.subject_code,
            room_code=rule.room_code,
            meeting_days=rule.meeting_days,
            time_slot=rule.time_slot,
            guidance_score=rule.guidance_score,
            support=rule.support,
            confidence=rule.confidence,
            lift=rule.lift,
            term_recurrence=rule.term_recurrence,
        )

    @classmethod
    def from_row(cls, row: ExtractedRule) -> "GuidanceRule":
        return cls(
            stream=PatternStream(row.pattern_type),
            faculty_id=row.faculty_id,
            subject_code=row.subject_code,
            room_code=row.room_code,
            meeting_days=row.meeting_days,
            time_slot=row.time_slot,
            guidance_score=row.guidance_score,
            support=row.support,
            confidence=row.confidence,
            lift=row.lift,
            term_recurrence=row.term_recurrence,
        )


@dataclass(frozen=True, slots=True)
class HistoricalGuidance:
    """An immutable, indexed rule set handed to the GA for one run.

    `rule_set_id` and `version` are absent for fixture guidance and present for
    a database rule set, so a run record can state which guidance produced it.
    """

    rules: tuple[GuidanceRule, ...]
    rule_set_id: UUID | None = None
    version: int | None = None
    _by_pattern: dict[_RuleKey, GuidanceRule] = field(
        init=False, repr=False, compare=False
    )
    _by_faculty: dict[tuple[PatternStream, UUID], tuple[GuidanceRule, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        by_pattern: dict[_RuleKey, GuidanceRule] = {}
        by_faculty: dict[tuple[PatternStream, UUID], list[GuidanceRule]] = {}
        for rule in self.rules:
            key = (rule.stream, rule.faculty_id, _comparison_key(*rule.consequent_key))
            if key in by_pattern:
                existing = by_pattern[key]
                if existing.consequent_key != rule.consequent_key:
                    raise GuidanceKeyCollisionError(existing, rule)
                raise ValueError(f"duplicate guidance rule: {key!r}")
            by_pattern[key] = rule
            by_faculty.setdefault((rule.stream, rule.faculty_id), []).append(rule)
        object.__setattr__(self, "_by_pattern", by_pattern)
        object.__setattr__(
            self,
            "_by_faculty",
            {key: tuple(value) for key, value in by_faculty.items()},
        )

    @classmethod
    def from_mined(
        cls,
        rules: Iterable[MinedRule],
        *,
        rule_set_id: UUID | None = None,
        version: int | None = None,
    ) -> "HistoricalGuidance":
        return cls(
            rules=tuple(GuidanceRule.from_mined(rule) for rule in rules),
            rule_set_id=rule_set_id,
            version=version,
        )

    def rules_for(
        self, stream: PatternStream, faculty_id: UUID
    ) -> tuple[GuidanceRule, ...]:
        return self._by_faculty.get((stream, faculty_id), ())

    def is_applicable(self, stream: PatternStream, faculty_id: UUID) -> bool:
        """Whether this stream holds any rule about this instructor at all.

        The scorer must ask this before dividing by a rule count: a faculty
        member the history says nothing about has no attainment to report, and
        scoring them zero would invent evidence of a broken pattern.
        """
        return bool(self.rules_for(stream, faculty_id))

    def score_for(
        self,
        stream: PatternStream,
        faculty_id: UUID,
        *,
        subject_code: str | None = None,
        room_code: str | None = None,
        meeting_days: str | None = None,
        time_slot: str | None = None,
    ) -> Decimal | None:
        """The rule's guidance score, or None when no rule covers the pattern.

        `time_slot` is compared as a time token, so an operational "LEC 07:30-09:00"
        finds a rule mined from a bare "07:30-09:00".
        """
        rule = self._by_pattern.get(
            (
                stream,
                faculty_id,
                _comparison_key(subject_code, room_code, meeting_days, time_slot),
            )
        )
        return None if rule is None else rule.guidance_score


class HistoricalRuleProvider(Protocol):
    def get_rules(self, rule_set_id: UUID | None = None) -> HistoricalGuidance: ...


class FixtureRuleProvider:
    """Serves fixed guidance, for GA work before real history is available."""

    def __init__(self, guidance: HistoricalGuidance):
        self._guidance = guidance

    @classmethod
    def from_rules(cls, rules: Sequence[GuidanceRule]) -> "FixtureRuleProvider":
        return cls(HistoricalGuidance(rules=tuple(rules)))

    def get_rules(self, rule_set_id: UUID | None = None) -> HistoricalGuidance:
        if rule_set_id is not None and rule_set_id != self._guidance.rule_set_id:
            raise RuleSetNotFoundError(rule_set_id)
        return self._guidance


class DatabaseRuleProvider:
    """Serves the active rule set, or a named one for audit and rollback.

    A draft set is retrievable by id — the administrator reviews it before
    activating — but never by omitting the id, which is how the GA asks for the
    guidance it is allowed to run with.
    """

    def __init__(self, session: Session):
        self._session = session

    def get_rules(self, rule_set_id: UUID | None = None) -> HistoricalGuidance:
        rule_set = (
            self._active_rule_set()
            if rule_set_id is None
            else self._rule_set(rule_set_id)
        )
        rows = self._session.scalars(
            select(ExtractedRule)
            .where(ExtractedRule.rule_set_id == rule_set.rule_set_id)
            # Row order is not guaranteed by the database and guidance is
            # compared between runs, so it is pinned here.
            .order_by(
                ExtractedRule.pattern_type,
                ExtractedRule.faculty_id,
                ExtractedRule.subject_code,
                ExtractedRule.room_code,
                ExtractedRule.meeting_days,
                ExtractedRule.time_slot,
            )
        ).all()
        return HistoricalGuidance(
            rules=tuple(GuidanceRule.from_row(row) for row in rows),
            rule_set_id=rule_set.rule_set_id,
            version=rule_set.version,
        )

    def _active_rule_set(self) -> RuleSet:
        rule_set = self._session.scalars(
            select(RuleSet).where(RuleSet.status == "active")
        ).one_or_none()
        if rule_set is None:
            raise NoActiveRuleSetError()
        return rule_set

    def _rule_set(self, rule_set_id: UUID) -> RuleSet:
        rule_set = self._session.get(RuleSet, rule_set_id)
        if rule_set is None:
            raise RuleSetNotFoundError(rule_set_id)
        return rule_set
