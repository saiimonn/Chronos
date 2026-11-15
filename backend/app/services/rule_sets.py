import logging
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import APIError
from app.models.app_user import AppUser
from app.models.extracted_rule import PATTERN_TYPES, ExtractedRule
from app.models.faculty import Faculty
from app.models.historical_snapshot import HistoricalRecord, HistoricalSnapshot
from app.models.rule_set import RuleSet, rule_set_term
from app.models.rule_set_faculty_metric import RuleSetFacultyMetric
from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream
from app.services.arm.providers import GuidanceRule, HistoricalGuidance
from app.services.audit import record_event
from app.services.jobs.codecs import encode_guidance_snapshot

logger = logging.getLogger(__name__)


def list_rule_sets(db: Session) -> list[dict]:
    """Return all rule sets with deterministic ordering and computed counts."""

    term_count_sq = (
        select(
            rule_set_term.c.rule_set_id,
            func.count().label("term_count"),
        )
        .select_from(rule_set_term)
        .group_by(rule_set_term.c.rule_set_id)
        .subquery()
    )

    rule_count_sq = (
        select(
            ExtractedRule.rule_set_id,
            func.count().label("rule_count"),
        )
        .select_from(ExtractedRule)
        .group_by(ExtractedRule.rule_set_id)
        .subquery()
    )

    rows = db.execute(
        select(
            RuleSet,
            func.coalesce(term_count_sq.c.term_count, 0).label("term_count"),
            func.coalesce(rule_count_sq.c.rule_count, 0).label("rule_count"),
        )
        .outerjoin(term_count_sq, RuleSet.rule_set_id == term_count_sq.c.rule_set_id)
        .outerjoin(rule_count_sq, RuleSet.rule_set_id == rule_count_sq.c.rule_set_id)
        .order_by(RuleSet.version.desc())
    ).all()

    return [
        {
            "rule_set_id": rule_set.rule_set_id,
            "version": rule_set.version,
            "version_label": rule_set.version_label,
            "status": rule_set.status,
            "generated_at": rule_set.generated_at,
            "activated_at": rule_set.activated_at,
            "notes": rule_set.notes,
            "term_count": term_count,
            "rule_count": rule_count,
        }
        for rule_set, term_count, rule_count in rows
    ]


def list_historical_terms(db: Session) -> list[dict]:
    """Return every captured historical term a new mining run may draw on.

    Read-only, and deliberately not a lifecycle operation: the administrator has
    to choose the terms before an ARM run is submitted, and a term whose academic
    year predates a stream's window contributes nothing to that stream. Publishing
    the per-stream eligibility here means the choice is made against the same
    policy the run will apply, instead of being silently narrowed afterwards.
    """
    record_count_sq = (
        select(
            HistoricalRecord.snapshot_id,
            func.count().label("record_count"),
        )
        .select_from(HistoricalRecord)
        .group_by(HistoricalRecord.snapshot_id)
        .subquery()
    )

    rows = db.execute(
        select(
            HistoricalSnapshot,
            func.coalesce(record_count_sq.c.record_count, 0).label("record_count"),
        )
        .outerjoin(
            record_count_sq,
            HistoricalSnapshot.snapshot_id == record_count_sq.c.snapshot_id,
        )
        .order_by(
            HistoricalSnapshot.academic_year.desc(),
            HistoricalSnapshot.term_number.desc(),
        )
    ).all()

    policy = HistoricalInclusionPolicy()

    def eligible_streams(academic_year: str) -> list[str]:
        try:
            return [
                stream.value
                for stream in PatternStream
                if policy.is_eligible(stream, academic_year)
            ]
        except ValueError:
            # A stored year the policy cannot parse is reported as eligible for
            # nothing rather than failing the whole listing: the administrator
            # still needs to see and select the terms that are usable.
            logger.warning(
                "historical_snapshot_unparsable_academic_year academic_year=%s",
                academic_year,
            )
            return []

    return [
        {
            "snapshot_id": snapshot.snapshot_id,
            "academic_year": snapshot.academic_year,
            "term_number": snapshot.term_number,
            "captured_at": snapshot.captured_at,
            "record_count": record_count,
            "eligible_streams": eligible_streams(snapshot.academic_year),
        }
        for snapshot, record_count in rows
    ]


def get_rule_set(db: Session, rule_set_id: UUID) -> RuleSet:
    """Return a single rule set with all nested relationships."""
    rule_set = db.scalars(
        select(RuleSet)
        .where(RuleSet.rule_set_id == rule_set_id)
        .options(
            selectinload(RuleSet.included_terms),
            selectinload(RuleSet.source_snapshots),
            selectinload(RuleSet.thresholds),
            selectinload(RuleSet.extracted_rules),
        )
    ).one_or_none()

    if rule_set is None:
        raise APIError(404, "NOT_FOUND", "The requested rule set was not found.")

    return rule_set


def get_rule_set_detail(db: Session, rule_set_id: UUID) -> dict:
    """Return one rule set's detail payload with every collection ordered.

    The nested collections come back from the relationship loads in whatever
    order the database hands them over, so they are sorted here: the review
    screen compares two versions side by side and rows that shuffle between
    requests make that comparison useless.
    """
    rule_set = get_rule_set(db, rule_set_id)
    stream_counts = Counter(rule.pattern_type for rule in rule_set.extracted_rules)

    return {
        "rule_set_id": rule_set.rule_set_id,
        "version": rule_set.version,
        "version_label": rule_set.version_label,
        "status": rule_set.status,
        "generated_at": rule_set.generated_at,
        "activated_at": rule_set.activated_at,
        "notes": rule_set.notes,
        "included_terms": sorted(
            rule_set.included_terms,
            key=lambda term: (term.academic_year, term.term_number),
        ),
        "thresholds": sorted(
            rule_set.thresholds,
            key=lambda threshold: threshold.pattern_type,
        ),
        "streams": [
            {"pattern_type": pattern_type, "rule_count": count}
            for pattern_type, count in sorted(stream_counts.items())
        ],
        "job_id": rule_set.job_id,
        "source_job_id": rule_set.source_job_id,
        "requested_by_user_id": rule_set.requested_by_user_id,
        "requested_by_username": (
            db.scalar(
                select(AppUser.username).where(
                    AppUser.user_id == rule_set.requested_by_user_id
                )
            )
            if rule_set.requested_by_user_id is not None
            else None
        ),
        "source_snapshots": sorted(
            rule_set.source_snapshots,
            key=lambda snapshot: (snapshot.academic_year, snapshot.term_number),
        ),
        "mining_summary": rule_set.mining_summary,
    }


def _rule_items(rule: ExtractedRule) -> tuple[list[str], list[str]]:
    """Split a rule's columns into antecedent and consequent display labels."""
    faculty = rule.faculty.full_name
    if rule.pattern_type == "teacher_subject_room":
        return [faculty, rule.subject_code], [rule.room_code]
    consequent = rule.subject_code or rule.meeting_days or rule.time_slot
    return [faculty], [consequent]


def list_rule_set_rules(
    db: Session,
    rule_set_id: UUID,
    pattern_type: str,
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    faculty_id: UUID | None = None,
) -> dict:
    """Return one page of a stream's rules, ordered by guidance score.

    `faculty_id` narrows the page to one teacher's rules (the roster reads a
    member's mined subjects this way).

    Read-only: the administrator inspects a version's candidate rules before
    activating it, and nothing here can change them. rule_id breaks score ties
    so a page boundary never shifts a rule between requests.
    """
    if db.get(RuleSet, rule_set_id) is None:
        raise APIError(404, "NOT_FOUND", "The requested rule set was not found.")

    stream = (ExtractedRule.rule_set_id == rule_set_id) & (
        ExtractedRule.pattern_type == pattern_type
    )
    if faculty_id is not None:
        stream = stream & (ExtractedRule.faculty_id == faculty_id)
    total = db.scalar(select(func.count()).select_from(ExtractedRule).where(stream))
    score = (
        ExtractedRule.guidance_score.asc()
        if order == "asc"
        else ExtractedRule.guidance_score.desc()
    )
    rules = db.scalars(
        select(ExtractedRule)
        .where(stream)
        .options(selectinload(ExtractedRule.faculty))
        .order_by(score, ExtractedRule.rule_id)
        .limit(limit)
        .offset(offset)
    ).all()

    items = []
    for rule in rules:
        antecedent, consequent = _rule_items(rule)
        items.append(
            {
                "rule_id": rule.rule_id,
                "pattern_type": rule.pattern_type,
                "faculty_id": rule.faculty_id,
                "antecedent": antecedent,
                "consequent": consequent,
                "support": rule.support,
                "confidence": rule.confidence,
                "lift": rule.lift,
                "term_recurrence": rule.term_recurrence,
                "guidance_score": rule.guidance_score,
            }
        )
    return {
        "pattern_type": pattern_type,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def list_rule_set_faculty_coverage(
    db: Session, rule_set_id: UUID, pattern_type: str
) -> dict:
    """Every teacher in one stream's history and how close they came to a rule.

    Read-only. Each row is the teacher's best candidate pattern with its four
    metrics and which thresholds it met, as recorded when the version was
    created. Versions created before that record existed fall back to their
    retained rules: every teacher with a rule, none of the ones without.
    """
    if db.get(RuleSet, rule_set_id) is None:
        raise APIError(404, "NOT_FOUND", "The requested rule set was not found.")

    roster = {
        faculty_id: is_active
        for faculty_id, is_active in db.execute(
            select(Faculty.faculty_id, Faculty.is_active)
        ).tuples()
    }
    stored = db.scalars(
        select(RuleSetFacultyMetric).where(
            RuleSetFacultyMetric.rule_set_id == rule_set_id,
            RuleSetFacultyMetric.pattern_type == pattern_type,
        )
    ).all()
    recorded = bool(stored) or bool(
        db.scalar(
            select(func.count())
            .select_from(RuleSetFacultyMetric)
            .where(RuleSetFacultyMetric.rule_set_id == rule_set_id)
        )
    )

    items = []
    if recorded:
        for row in stored:
            items.append(
                {
                    "source_faculty_key": row.source_faculty_key,
                    "faculty_id": row.faculty_id,
                    "faculty_name": row.faculty_name,
                    "on_roster": row.faculty_id in roster,
                    "is_active": roster.get(row.faculty_id),
                    "has_rule": row.has_rule,
                    "subject_code": row.subject_code,
                    "room_code": row.room_code,
                    "meeting_days": row.meeting_days,
                    "time_slot": row.time_slot,
                    "offering_count": row.offering_count,
                    "support": row.support,
                    "confidence": row.confidence,
                    "lift": row.lift,
                    "term_recurrence": row.term_recurrence,
                    "meets_support": row.meets_support,
                    "meets_confidence": row.meets_confidence,
                    "meets_lift": row.meets_lift,
                    "meets_term_recurrence": row.meets_term_recurrence,
                }
            )
    else:
        best: dict[UUID, ExtractedRule] = {}
        for rule in db.scalars(
            select(ExtractedRule)
            .where(
                ExtractedRule.rule_set_id == rule_set_id,
                ExtractedRule.pattern_type == pattern_type,
            )
            .options(selectinload(ExtractedRule.faculty))
            .order_by(ExtractedRule.guidance_score.desc(), ExtractedRule.rule_id)
        ):
            best.setdefault(rule.faculty_id, rule)
        for faculty_id, rule in best.items():
            items.append(
                {
                    "source_faculty_key": str(faculty_id),
                    "faculty_id": faculty_id,
                    "faculty_name": rule.faculty.full_name,
                    "on_roster": True,
                    "is_active": roster.get(faculty_id),
                    "has_rule": True,
                    "subject_code": rule.subject_code,
                    "room_code": rule.room_code,
                    "meeting_days": rule.meeting_days,
                    "time_slot": rule.time_slot,
                    "offering_count": None,
                    "support": rule.support,
                    "confidence": rule.confidence,
                    "lift": rule.lift,
                    "term_recurrence": rule.term_recurrence,
                    # A retained rule cleared every threshold by definition.
                    "meets_support": True,
                    "meets_confidence": True,
                    "meets_lift": True,
                    "meets_term_recurrence": True,
                }
            )

    def met(item: dict) -> int:
        return sum(
            item[key]
            for key in (
                "meets_support",
                "meets_confidence",
                "meets_lift",
                "meets_term_recurrence",
            )
        )

    # Teachers with rules first, then the closest misses; names break ties so
    # the order is stable between requests.
    items.sort(
        key=lambda item: (
            not item["has_rule"],
            -met(item),
            -item["support"],
            item["faculty_name"].casefold(),
        )
    )
    with_rule = sum(1 for item in items if item["has_rule"])
    # Across all four streams, so the page can say how many teachers the version
    # guides at all; a per-stream count alone reads lower than that.
    version_with_rule = db.scalar(
        select(func.count(func.distinct(ExtractedRule.faculty_id))).where(
            ExtractedRule.rule_set_id == rule_set_id
        )
    )
    return {
        "pattern_type": pattern_type,
        "metrics_recorded": recorded,
        "version_with_rule_count": version_with_rule,
        "teacher_count": len(items),
        "with_rule_count": with_rule,
        "without_rule_count": len(items) - with_rule,
        "items": items,
    }


def validate_rule_set(rule_set: RuleSet) -> None:
    """Validate that a rule set satisfies all structural integrity constraints.

    Note: Database CHECK constraints on extracted_rule and rule_set_threshold
    enforce column domain ranges at table level. Application-level validation provides
    defense-in-depth and enforces aggregate structural integrity across collections
    (e.g., non-empty terms, exactly four streams, non-empty rules, non-blank strings).

    Constraints:
    1. Provenance: At least one included historical term.
    2. Streams: Exactly four threshold streams (teacher_subject, teacher_day,
       teacher_time, teacher_subject_room).
    3. Threshold ranges: Valid support, confidence, lift, recurrence, and academic year.
    4. Non-empty rules: At least one extracted rule exists.
    5. Rules: Every rule has valid pattern_type, non-null faculty_id,
       properly populated consequent columns matching the pattern type,
       and valid metric ranges.
    """
    if not rule_set.included_terms:
        raise APIError(
            409,
            "RULE_SET_INVALID",
            "Rule set must include at least one historical term.",
        )

    threshold_patterns = {t.pattern_type for t in rule_set.thresholds}
    if len(rule_set.thresholds) != 4 or threshold_patterns != set(PATTERN_TYPES):
        raise APIError(
            409,
            "RULE_SET_INVALID",
            f"Rule set must define exactly the four required threshold streams: {', '.join(PATTERN_TYPES)}.",
        )

    for threshold in rule_set.thresholds:
        if not (0 < threshold.min_support <= 1):
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Threshold for {threshold.pattern_type} has invalid min_support: {threshold.min_support}.",
            )
        if not (0 < threshold.min_confidence <= 1):
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Threshold for {threshold.pattern_type} has invalid min_confidence: {threshold.min_confidence}.",
            )
        if threshold.min_lift < 0:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Threshold for {threshold.pattern_type} has negative min_lift: {threshold.min_lift}.",
            )
        if threshold.min_term_recurrence < 1:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Threshold for {threshold.pattern_type} has non-positive min_term_recurrence: {threshold.min_term_recurrence}.",
            )
        if not threshold.earliest_academic_year or not str(threshold.earliest_academic_year).strip():
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Threshold for {threshold.pattern_type} is missing earliest_academic_year.",
            )

    if not rule_set.extracted_rules:
        raise APIError(
            409,
            "RULE_SET_INVALID",
            "Rule set must contain at least one extracted rule.",
        )

    for rule in rule_set.extracted_rules:
        if rule.pattern_type not in PATTERN_TYPES:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has unrecognized pattern_type '{rule.pattern_type}'.",
            )
        if rule.faculty_id is None:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} is missing faculty_id.",
            )

        pt = rule.pattern_type
        if pt == "teacher_subject":
            valid = (
                rule.subject_code is not None
                and rule.room_code is None
                and rule.meeting_days is None
                and rule.time_slot is None
            )
        elif pt == "teacher_day":
            valid = (
                rule.meeting_days is not None
                and rule.subject_code is None
                and rule.room_code is None
                and rule.time_slot is None
            )
        elif pt == "teacher_time":
            valid = (
                rule.time_slot is not None
                and rule.subject_code is None
                and rule.room_code is None
                and rule.meeting_days is None
            )
        elif pt == "teacher_subject_room":
            valid = (
                rule.subject_code is not None
                and rule.room_code is not None
                and rule.meeting_days is None
                and rule.time_slot is None
            )
        else:
            valid = False

        if not valid:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has invalid consequent columns for stream '{pt}'.",
            )

        if not (0 < rule.support <= 1):
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has invalid support: {rule.support}.",
            )
        if not (0 < rule.confidence <= 1):
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has invalid confidence: {rule.confidence}.",
            )
        if rule.lift < 0:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has negative lift: {rule.lift}.",
            )
        if rule.term_recurrence < 1:
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has non-positive term_recurrence: {rule.term_recurrence}.",
            )
        if not (0 <= rule.guidance_score <= 1):
            raise APIError(
                409,
                "RULE_SET_INVALID",
                f"Rule {rule.rule_id} has invalid guidance_score: {rule.guidance_score}.",
            )

    # Encode the exact snapshot GA submission enqueues, so a set whose rules
    # collide once time tokens are normalized, or that the stricter job
    # decoder rejects, is refused before the incumbent is superseded.
    try:
        encode_guidance_snapshot(
            HistoricalGuidance(
                rules=tuple(
                    GuidanceRule.from_row(rule) for rule in rule_set.extracted_rules
                ),
                rule_set_id=rule_set.rule_set_id,
                version=rule_set.version,
            )
        )
    except ValueError as exc:
        raise APIError(
            409,
            "RULE_SET_INVALID",
            f"Rule set cannot be loaded as runtime guidance: {exc}",
        ) from exc


def _transition_rule_set(
    db: Session,
    rule_set_id: UUID,
    expected_status: str,
    action: str,
    actor_id: UUID | None = None,
) -> dict:
    """Atomically validate and transition a RuleSet to active status.

    In PostgreSQL, acquires a table lock in SHARE ROW EXCLUSIVE mode to serialize
    concurrent transitions while allowing concurrent non-exclusive reads. Then
    locks both the target row and the incumbent active row, validates structural
    constraints, supersedes the incumbent, sets the target status to active and
    updates activated_at.

    Loser of any concurrent conflict rolls back cleanly with zero partial changes.
    """
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("LOCK TABLE rule_set IN SHARE ROW EXCLUSIVE MODE"))

    target = db.scalars(
        select(RuleSet)
        .where(RuleSet.rule_set_id == rule_set_id)
        .options(
            selectinload(RuleSet.included_terms),
            selectinload(RuleSet.thresholds),
            selectinload(RuleSet.extracted_rules),
        )
        .with_for_update()
    ).one_or_none()

    if target is None:
        logger.warning(
            "rule_set_%s_not_found actor_id=%s rule_set_id=%s",
            action,
            actor_id,
            rule_set_id,
        )
        raise APIError(404, "NOT_FOUND", "The requested rule set was not found.")

    if target.status != expected_status:
        logger.warning(
            "rule_set_%s_invalid_status actor_id=%s rule_set_id=%s status=%s expected=%s",
            action,
            actor_id,
            rule_set_id,
            target.status,
            expected_status,
        )
        if target.status == "active":
            raise APIError(409, "RULE_SET_STATUS_CONFLICT", "Rule set is already active.")
        if expected_status == "draft":
            raise APIError(
                409,
                "RULE_SET_STATUS_CONFLICT",
                f"Cannot activate rule set in status '{target.status}'; must be 'draft'.",
            )
        else:
            raise APIError(
                409,
                "RULE_SET_STATUS_CONFLICT",
                f"Cannot rollback rule set in status '{target.status}'; must be 'superseded'.",
            )

    try:
        validate_rule_set(target)
    except APIError as exc:
        logger.warning(
            "rule_set_%s_validation_failed actor_id=%s rule_set_id=%s reason=%s",
            action,
            actor_id,
            rule_set_id,
            exc.message,
        )
        raise

    incumbent = db.scalars(
        select(RuleSet).where(RuleSet.status == "active").with_for_update()
    ).one_or_none()

    if incumbent is not None and incumbent.rule_set_id != target.rule_set_id:
        incumbent.status = "superseded"
        db.flush()

    target.status = "active"
    target.activated_at = datetime.now(timezone.utc)
    record_event(
        db,
        actor_id=actor_id,
        operation=f"rule_set.{action}",
        target_type="rule_set",
        target_id=target.rule_set_id,
        details={
            "version": target.version,
            "validation": "passed",
            "before": {
                "status": expected_status,
                "active_rule_set_id": incumbent.rule_set_id if incumbent else None,
                "active_version": incumbent.version if incumbent else None,
            },
            "after": {"status": "active"},
        },
    )
    db.flush()

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "rule_set_%s_integrity_error actor_id=%s rule_set_id=%s exc=%s",
            action,
            actor_id,
            rule_set_id,
            exc,
        )
        raise APIError(
            409,
            "RULE_SET_STATUS_CONFLICT",
            "A concurrent rule set activation conflicted with this operation.",
        ) from exc

    logger.info(
        "rule_set_%s_success actor_id=%s rule_set_id=%s version=%s",
        action,
        actor_id,
        target.rule_set_id,
        target.version,
    )

    return get_rule_set_detail(db, target.rule_set_id)


def activate_rule_set(
    db: Session,
    rule_set_id: UUID,
    actor_id: UUID | None = None,
) -> dict:
    """Explicitly activate a draft RuleSet after structural validation."""
    return _transition_rule_set(
        db=db,
        rule_set_id=rule_set_id,
        expected_status="draft",
        action="activate",
        actor_id=actor_id,
    )


def rollback_rule_set(
    db: Session,
    rule_set_id: UUID,
    actor_id: UUID | None = None,
) -> dict:
    """Explicitly rollback to a superseded RuleSet through the same validation path."""
    return _transition_rule_set(
        db=db,
        rule_set_id=rule_set_id,
        expected_status="superseded",
        action="rollback",
        actor_id=actor_id,
    )
