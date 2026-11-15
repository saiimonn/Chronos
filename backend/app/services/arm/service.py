"""CHR-33 / CHR-ARM-002 rule-mining run: history in, candidate rule set out.

A run only ever writes a `draft` rule set. The active set is never touched here,
so a failed or empty run leaves the guidance the GA is using exactly as it was
(FR-ARM-005); the administrator activates a draft separately after reviewing the
summary this service returns.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.curriculum import CurriculumRequirement
from app.models.extracted_rule import ExtractedRule
from app.models.faculty import Faculty
from app.models.historical_snapshot import HistoricalSnapshot
from app.models.room import Room
from app.models.rule_set import RuleSet, RuleSetThreshold
from app.models.subject import Subject
from app.models.term import Term
from app.services.catalog.curriculum import governing_curriculum_ids
from app.services.arm.faculty_metric_rows import (
    faculty_metric_rows,
    version_faculty_metrics,
)
from app.services.arm.mining import (
    CandidateRuleSet,
    MinedRule,
    MiningSkipReason,
    MiningThresholds,
    RuleMiner,
)
from app.services.arm.preprocessing import (
    HistoricalInclusionPolicy,
    HistoricalOffering,
    HistoricalTerm,
    MiningMode,
    PatternStream,
    prepare_training_data,
)


class MiningProducedNoRulesError(RuntimeError):
    """Every stream was empty, so there is no candidate worth versioning."""

    def __init__(self) -> None:
        super().__init__("rule mining retained no rules")


@dataclass(frozen=True, slots=True)
class StreamSummary:
    stream: PatternStream
    training_term_count: int
    transaction_count: int
    retained_rule_count: int
    persisted_rule_count: int
    skipped_reason: MiningSkipReason | None


@dataclass(frozen=True, slots=True)
class MiningRunSummary:
    """What the administrator reviews before activating the draft."""

    rule_set_id: UUID
    version: int
    streams: tuple[StreamSummary, ...]
    included_term_count: int
    # Rules whose faculty, subject, or room is no longer in the operational
    # catalog. They are reported rather than dropped silently: guidance about a
    # retired room is unusable to the GA, but a long list here means the catalog
    # and the history have drifted apart and someone should look.
    unknown_catalog_reference_count: int

    @property
    def persisted_rule_count(self) -> int:
        return sum(stream.persisted_rule_count for stream in self.streams)


def load_historical_terms(session: Session) -> tuple[HistoricalTerm, ...]:
    """Read the immutable history into the DTOs the ARM pipeline mines."""
    return _to_historical_terms(session, select(HistoricalSnapshot))


def load_historical_terms_by_ids(
    session: Session, snapshot_ids: list[UUID]
) -> tuple[HistoricalTerm, ...]:
    """Read specific historical snapshots by their IDs into DTOs for ARM mining."""
    return _to_historical_terms(
        session,
        select(HistoricalSnapshot).where(
            HistoricalSnapshot.snapshot_id.in_(snapshot_ids)
        ),
    )


def _to_historical_terms(session: Session, query) -> tuple[HistoricalTerm, ...]:
    snapshots = session.scalars(
        query.order_by(
            HistoricalSnapshot.academic_year,
            HistoricalSnapshot.term_number,
            HistoricalSnapshot.snapshot_id,
        )
    ).all()
    # Only subjects a reviewed curriculum still requires are current offerings
    # (thesis §4.1). The filter runs here, before transactions are built:
    # teacher-day and teacher-time rules carry no subject, so a record from a
    # discontinued curriculum could not be filtered out of them afterwards.
    current_subjects = _current_subject_codes(session)
    return tuple(
        HistoricalTerm(
            snapshot_id=snapshot.snapshot_id,
            academic_year=snapshot.academic_year,
            term_number=snapshot.term_number,
            offerings=tuple(
                HistoricalOffering(
                    historical_record_id=record.historical_record_id,
                    faculty_id=record.source_faculty_id,
                    subject_code=record.subject_code,
                    meeting_days=record.meeting_days,
                    time_slot=record.time_slot,
                    room_code=record.room_code,
                )
                for record in snapshot.records
                if record.subject_code in current_subjects
            ),
        )
        for snapshot in snapshots
    )


def _current_subject_codes(session: Session) -> set[str]:
    """Subjects required by a curriculum some current cohort still follows.

    A superseded prospectus stays on file for the cohorts that entered under
    it; once none remain, its retired subjects are no longer current. Cohorts
    are current relative to the active term. Without one there is no current
    cohort to resolve, so every recorded curriculum counts.
    """
    query = select(CurriculumRequirement.subject_code).distinct()
    academic_year = session.scalar(
        select(Term.academic_year).where(Term.is_active)
    )
    if academic_year is not None:
        query = query.where(
            CurriculumRequirement.curriculum_id.in_(
                governing_curriculum_ids(session, academic_year)
            )
        )
    return set(session.scalars(query))


class RuleMiningService:
    def __init__(
        self,
        session: Session,
        miner: RuleMiner | None = None,
        policy: HistoricalInclusionPolicy | None = None,
    ):
        self._session = session
        self._miner = miner or RuleMiner()
        self._policy = policy or HistoricalInclusionPolicy()

    def create_candidate(
        self,
        thresholds: MiningThresholds | Mapping[PatternStream, MiningThresholds],
        notes: str | None = None,
    ) -> MiningRunSummary:
        terms = load_historical_terms(self._session)
        prepared = prepare_training_data(
            terms, self._policy, mode=MiningMode.OPERATIONAL
        )
        candidate = self._miner.mine(prepared, thresholds)
        if candidate.is_empty:
            raise MiningProducedNoRulesError()

        rule_set = RuleSet(version=self._next_version(), status="draft", notes=notes)
        self._session.add(rule_set)
        self._session.flush()

        self._add_thresholds(rule_set, candidate)
        summaries, unknown = self._add_rules(rule_set, candidate, prepared)
        included_terms = self._link_included_terms(rule_set, prepared)
        self._session.flush()

        return MiningRunSummary(
            rule_set_id=rule_set.rule_set_id,
            version=rule_set.version,
            streams=summaries,
            included_term_count=included_terms,
            unknown_catalog_reference_count=unknown,
        )

    def _next_version(self) -> int:
        highest = self._session.scalar(select(func.max(RuleSet.version)))
        return (highest or 0) + 1

    def _add_thresholds(self, rule_set: RuleSet, candidate: CandidateRuleSet) -> None:
        # Written for every stream, including one that produced nothing: the
        # threshold row is the record of what was asked for, and its absence
        # would read as "this stream was not part of the run".
        for result in candidate.streams:
            self._session.add(
                RuleSetThreshold(
                    rule_set_id=rule_set.rule_set_id,
                    pattern_type=str(result.stream),
                    min_support=result.thresholds.min_support,
                    min_confidence=result.thresholds.min_confidence,
                    min_lift=result.thresholds.min_lift,
                    min_term_recurrence=result.thresholds.min_term_recurrence,
                    earliest_academic_year=self._policy.earliest_academic_year(
                        result.stream
                    ),
                )
            )

    def _add_rules(
        self, rule_set: RuleSet, candidate: CandidateRuleSet, prepared
    ) -> tuple[tuple[StreamSummary, ...], int]:
        roster = dict(
            self._session.execute(select(Faculty.faculty_id, Faculty.full_name))
            .tuples()
            .all()
        )
        known_faculty = set(roster)
        persisted_pairs: set[tuple[str, UUID]] = set()
        known_subjects = set(self._session.scalars(select(Subject.subject_code)).all())
        known_rooms = set(self._session.scalars(select(Room.room_code)).all())

        summaries: list[StreamSummary] = []
        unknown = 0
        for result in candidate.streams:
            persisted = 0
            for rule in result.rules:
                if not self._is_in_catalog(
                    rule, known_faculty, known_subjects, known_rooms
                ):
                    unknown += 1
                    continue
                self._session.add(
                    ExtractedRule(
                        rule_set_id=rule_set.rule_set_id,
                        pattern_type=str(rule.stream),
                        faculty_id=rule.faculty_id,
                        subject_code=rule.subject_code,
                        room_code=rule.room_code,
                        meeting_days=rule.meeting_days,
                        time_slot=rule.time_slot,
                        support=rule.support,
                        confidence=rule.confidence,
                        lift=rule.lift,
                        term_recurrence=rule.term_recurrence,
                        guidance_score=rule.guidance_score,
                    )
                )
                persisted_pairs.add((str(rule.stream), rule.faculty_id))
                persisted += 1
            summaries.append(
                StreamSummary(
                    stream=result.stream,
                    training_term_count=result.training_term_count,
                    transaction_count=result.transaction_count,
                    retained_rule_count=len(result.rules),
                    persisted_rule_count=persisted,
                    skipped_reason=result.skipped_reason,
                )
            )
        self._session.add_all(
            faculty_metric_rows(
                rule_set.rule_set_id,
                version_faculty_metrics(prepared, candidate),
                persisted=persisted_pairs,
                roster=roster,
            )
        )
        return tuple(summaries), unknown

    @staticmethod
    def _is_in_catalog(
        rule: MinedRule,
        known_faculty: set[UUID],
        known_subjects: set[str],
        known_rooms: set[str],
    ) -> bool:
        if rule.faculty_id not in known_faculty:
            return False
        if rule.subject_code is not None and rule.subject_code not in known_subjects:
            return False
        if rule.room_code is not None and rule.room_code not in known_rooms:
            return False
        return True

    def _link_included_terms(self, rule_set: RuleSet, prepared) -> int:
        """Record the terms the run actually mined, across all four streams.

        The union is taken because the streams do not share a starting year: a
        2018 term contributes to teacher-subject alone, and omitting it would
        misstate what the rule set was built from.
        """
        snapshot_ids = {
            term.snapshot_id
            for data in prepared
            for term in data.mined_terms
        }
        if not snapshot_ids:
            return 0
        source_term_ids = set(
            self._session.scalars(
                select(HistoricalSnapshot.source_term_id).where(
                    HistoricalSnapshot.snapshot_id.in_(snapshot_ids)
                )
            ).all()
        )
        # A snapshot keeps its term reference as a plain column, so the
        # operational row it names may have been removed since. History is
        # unaffected; the link is simply not available for that term.
        terms = self._session.scalars(
            select(Term).where(Term.term_id.in_(source_term_ids))
        ).all()
        rule_set.included_terms.extend(terms)
        return len(terms)
