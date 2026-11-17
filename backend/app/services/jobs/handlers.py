"""CHR-31 / CHR-WORKER-001 pure child-process job handlers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.domain import CandidateSchedule
from app.services.arm.mining import CandidateRuleSet, RuleMiner
from app.services.arm.preprocessing import (
    HistoricalTerm,
    MiningMode,
    prepare_training_data,
)
from app.services.ga.day_balance import DEFAULT_DAY_BALANCE_WEIGHT
from app.services.ga.engine import GAEngine
from app.services.ga.operators import BLOCK_DAY_SPAN_LIMIT_MINUTES
from app.services.ga.fitness import ZeroHistoricalAttainmentScorer
from app.services.ga.metrics import GARunMetrics
from app.services.ga.rule_scoring import SCORING_POLICY, RuleBackedHistoricalScorer
from app.services.jobs.codecs import (
    CODEC_VERSION,
    JsonObject,
    decode_block_generation_context,
    decode_ga_config,
    decode_guidance_snapshot,
    decode_historical_terms,
    decode_mining_thresholds,
    decode_scheduling_problem,
    require_version,
)
from app.services.jobs.outcomes import JobDomainError, ensure_parent_alive


def _encode_ga_result(
    candidate: JsonObject,
    metrics: JsonObject,
    *,
    locks_enforced: bool | None = None,
) -> JsonObject:
    """Shared result envelope for term-wide and per-Block GA runs."""
    envelope: JsonObject = {
        "version": CODEC_VERSION,
        "candidate": candidate,
        "metrics": metrics,
    }
    if locks_enforced is not None:
        envelope["locks_enforced"] = locks_enforced
    return envelope


def _encode_candidate_metrics(result_metrics: GARunMetrics) -> JsonObject:
    """Encode GARunMetrics into the public JSON shape."""
    return {
        "random_seed": result_metrics.random_seed,
        "config": {
            "population_size": result_metrics.config.population_size,
            "max_generations": result_metrics.config.max_generations,
            "crossover_rate": result_metrics.config.crossover_rate,
            "mutation_rate": result_metrics.config.mutation_rate,
            "gene_mutation_rate": result_metrics.config.gene_mutation_rate,
            "tournament_size": result_metrics.config.tournament_size,
            "elite_count": result_metrics.config.elite_count,
            "stagnation_limit": result_metrics.config.stagnation_limit,
            "random_seed": result_metrics.config.random_seed,
            "meeting_day_patterns": list(result_metrics.config.meeting_day_patterns),
        },
        "termination_reason": result_metrics.termination_reason.value,
        "termination_generation": result_metrics.termination_generation,
        "first_feasible_generation": result_metrics.first_feasible_generation,
        "elapsed_seconds": result_metrics.elapsed_seconds,
        "generations": [
            {
                "generation": item.generation,
                "best_fitness": list(item.best_fitness),
                "mean_assignment_completeness": (item.mean_assignment_completeness),
            }
            for item in result_metrics.generations
        ],
        "required_class_count": result_metrics.required_class_count,
        "assigned_class_count": result_metrics.assigned_class_count,
        "assignment_completeness": result_metrics.assignment_completeness,
        "hard_conflict_count": result_metrics.hard_conflict_count,
        "historical_pattern_attainment": (result_metrics.historical_pattern_attainment),
        "selected_hard_conflict_count": result_metrics.selected_hard_conflict_count,
        "combined_hard_conflict_count": result_metrics.combined_hard_conflict_count,
        "cross_block_conflict_count": result_metrics.cross_block_conflict_count,
    }


def _encode_candidate(candidate: CandidateSchedule) -> JsonObject:
    """Encode a CandidateSchedule into the public JSON shape."""
    return {
        "term_id": str(candidate.term_id),
        "schedule_id": (
            str(candidate.schedule_id) if candidate.schedule_id is not None else None
        ),
        "assignments": [
            {
                "class_id": str(item.class_id),
                "faculty_id": str(item.faculty_id),
                "time_slot_id": str(item.time_slot_id),
                "room_code": item.room_code,
                "meeting_days": item.meeting_days,
            }
            for item in candidate.assignments
        ],
    }


_PUBLISHED_SECONDS = "published_attempt_processing_seconds"
# Block day lengths a per-Block run tries, in order: 8 hours, then longer
# only when a shorter day cannot hold every class without a conflict.
BLOCK_DAY_SPAN_RETRIES = (8 * 60, 9 * 60, BLOCK_DAY_SPAN_LIMIT_MINUTES)


def run_ga(payload: Mapping[str, Any]) -> JsonObject:
    require_version(payload)
    if "block_context" in payload:
        return _run_ga_block(payload)
    return _run_ga_term(payload)


def _run_ga_term(payload: Mapping[str, Any]) -> JsonObject:
    started = perf_counter()  # CHR-192: whole attempt, from before input decode
    job_id_raw = payload.get("job_id")
    job_id = UUID(job_id_raw) if job_id_raw is not None else None
    claim_token_raw = payload.get("claim_token")
    claim_token = UUID(claim_token_raw) if claim_token_raw is not None else None
    problem = decode_scheduling_problem(payload["problem"])
    guidance = decode_guidance_snapshot(payload)
    # CHR-185: a retry after publication reports the stored run, not a new one.
    published = _published_ga_result(job_id, claim_token)
    if published is not None:
        return published
    scorer = (
        RuleBackedHistoricalScorer(guidance)
        if guidance is not None
        else ZeroHistoricalAttainmentScorer()
    )
    result = GAEngine(
        problem,
        decode_ga_config(payload["config"]),
        historical_scorer=scorer,
    ).run()
    # The ID is fixed before publication so the stored result can carry it.
    candidate = replace(result.candidate, schedule_id=uuid4())
    output = _encode_ga_result(
        _encode_candidate(candidate),
        _encode_candidate_metrics(result.metrics),
    )
    output["historical_guidance"] = _guidance_result(
        guidance, scorer.score(problem, result.candidate.assignments)
    )
    # CHR-192: stored with the schedule so a reuse can say how long it took.
    output[_PUBLISHED_SECONDS] = perf_counter() - started
    stored = _persist_ga_schedule(candidate, job_id, claim_token, output)
    return {k: v for k, v in stored.items() if k != _PUBLISHED_SECONDS}


def _run_ga_block(payload: Mapping[str, Any]) -> JsonObject:
    """Per-Block GA: decodes a BlockGenerationContext and runs the GA.

    GAEngine.from_block_context initializes the GA with the selected Block's
    classes and enforces locked assignments from other Blocks inside the GA
    loop (CHR-SCH-010); this lock enforcement is recorded explicitly in the
    result envelope.
    """
    context = decode_block_generation_context(payload["block_context"])
    # A context queued before locks carried Offering identity and units would
    # exempt unrelated Offerings and count accepted load as zero, so the run
    # could not honestly report its locks as enforced.
    if any(lock.offering_id is None for lock in context.locked_assignments):
        raise ValueError(
            "locked assignments lack Offering identity and teaching units; "
            "resubmit the Block generation job"
        )
    guidance = decode_guidance_snapshot(payload)
    scorer = (
        RuleBackedHistoricalScorer(guidance)
        if guidance is not None
        else ZeroHistoricalAttainmentScorer()
    )
    config = decode_ga_config(payload["config"])
    # The Block day is held to 8 hours. Only when a run cannot place every
    # class without a conflict is it retried with a longer day, and the first
    # complete, conflict-free result wins; failing that, the one placing the
    # most classes, then with the fewest conflicts.
    result = None
    for span_minutes in BLOCK_DAY_SPAN_RETRIES:
        attempt = GAEngine.from_block_context(
            context,
            config,
            historical_scorer=scorer,
            # Master-schedule generation also evens out each Block's week.
            day_balance_weight=DEFAULT_DAY_BALANCE_WEIGHT,
            # A proposal must never come back double-booked: see GAEngine.
            repair_initial_population=True,
            block_day_span_minutes=span_minutes,
        ).run()
        rank = (
            attempt.metrics.assigned_class_count,
            -attempt.metrics.combined_hard_conflict_count,
        )
        if result is None or rank > best_rank:
            result, best_rank, used_span = attempt, rank, span_minutes
        if (
            attempt.metrics.assigned_class_count
            == attempt.metrics.required_class_count
            and attempt.metrics.combined_hard_conflict_count == 0
        ):
            break
    # The partner Block's LABs were placed only to keep each pair together:
    # they are reserved for that Block, not part of this Block's proposal.
    own_class_ids = {item.class_id for item in context.selected_classes}
    own = replace(
        result.candidate,
        assignments=tuple(
            item for item in result.candidate.assignments if item.class_id in own_class_ids
        ),
    )
    reserved = replace(
        result.candidate,
        assignments=tuple(
            item
            for item in result.candidate.assignments
            if item.class_id not in own_class_ids
        ),
    )
    output = _encode_ga_result(
        _encode_candidate(own),
        _encode_candidate_metrics(result.metrics),
        locks_enforced=True,
    )
    # Over 8 hours only when the Block could not be placed in 8.
    output["block_day_span_minutes"] = used_span
    if context.partner_lab_classes:
        output["partner_lab_reservations"] = _encode_candidate(reserved)["assignments"]
    output["historical_guidance"] = _guidance_result(
        guidance, scorer.score(context.selected_problem, own.assignments)
    )
    return output


def _guidance_result(guidance, breakdown) -> JsonObject:
    return {
        "scoring_policy": SCORING_POLICY,
        "mode": "guided" if guidance is not None else "unguided",
        "rule_set_id": str(guidance.rule_set_id) if guidance is not None else None,
        "rule_set_version": guidance.version if guidance is not None else None,
        "rule_count": len(guidance.rules) if guidance is not None else 0,
        "scores": {
            "teacher_subject_score": breakdown.teacher_subject_score,
            "teacher_day_score": breakdown.teacher_day_score,
            "teacher_time_score": breakdown.teacher_time_score,
            "teacher_subject_room_score": breakdown.teacher_subject_room_score,
            "historical_pattern_attainment": breakdown.historical_pattern_attainment,
        },
    }


def _stored_ga_result(schedule) -> JsonObject:
    result = schedule.publication_result
    if (
        not isinstance(result, dict)
        or result.get("candidate", {}).get("schedule_id") != str(schedule.schedule_id)
    ):
        raise ValueError("GA publication result is unavailable")
    return result


def _published_ga_result(
    job_id: UUID | None, claim_token: UUID | None,
) -> JsonObject | None:
    """CHR-185: the result an earlier attempt committed, under the live claim."""
    import os

    if os.environ.get("GA_SKIP_PERSIST"):
        return None
    if job_id is None or claim_token is None:
        raise ValueError("GA publication requires a live worker claim")

    from app.db.session import SessionLocal
    from app.services.schedules import find_published_schedule

    with SessionLocal.begin() as session:
        schedule = find_published_schedule(session, job_id, claim_token)
        if schedule is None:
            return None
        stored = dict(_stored_ga_result(schedule))
        # CHR-192: flag the reuse; null when published before the time was stored.
        stored[_PUBLISHED_SECONDS] = stored.get(_PUBLISHED_SECONDS)
        stored["reused_published_result"] = True
        return stored


def _persist_ga_schedule(
    candidate: CandidateSchedule,
    job_id: UUID | None,
    claim_token: UUID | None,
    output: JsonObject,
) -> JsonObject:
    """CHR-165 / CHR-FIX-005: publish only under the current worker claim.

    ``persist_candidate`` owns the fenced commit and stores *output* with the
    records (CHR-185); the stored result is returned, so a schedule an earlier
    attempt published wins over this run. ``GA_SKIP_PERSIST=1`` bypasses the
    write for supervisor tests without a live database.
    """
    import os

    if os.environ.get("GA_SKIP_PERSIST"):
        return output

    # CHR-197: an orphaned child must not publish after its owner is gone.
    ensure_parent_alive()

    if job_id is None or claim_token is None:
        raise ValueError("GA publication requires a live worker claim")

    from app.db.session import SessionLocal
    from app.services.schedules import persist_candidate

    schedule_candidate = replace(candidate, job_id=job_id)
    with SessionLocal() as session:
        schedule = persist_candidate(
            session, schedule_candidate, claim_token=claim_token, result=output,
        )
        return _stored_ga_result(schedule)


def _persist_draft_ruleset(
    session: Session,
    candidate: CandidateRuleSet,
    historical_terms: tuple[HistoricalTerm, ...],
    notes: str | None,
    *,
    job_id: UUID,
    claim_token: UUID,
    result: JsonObject | None = None,
    requested_by_user_id: UUID | None = None,
) -> str:
    """Persist a draft RuleSet, thresholds, rules, provenance and summary.

    CHR-126 / CHR-ARM-006 fences the entire atomic publication, including the
    CHR-129 persisted summary and immutable recovery result. Returns the ID
    without replacing an existing publication. A run whose catalog filtering
    leaves no rule raises, so no empty version is published.
    """
    from uuid import uuid4

    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from app.core.config import settings
    from app.models.execution_job import JobType
    from app.models.extracted_rule import ExtractedRule
    from app.models.faculty import Faculty
    from app.models.historical_snapshot import HistoricalSnapshot
    from app.models.room import Room
    from app.models.rule_set import RuleSet, RuleSetThreshold
    from app.models.subject import Subject
    from app.models.term import Term
    from app.repositories.jobs import JobRepository
    from app.services.arm.preprocessing import HistoricalInclusionPolicy

    policy = HistoricalInclusionPolicy()
    repository = JobRepository(session)
    if not repository.lock_live_claim(job_id, claim_token, job_type=JobType.ARM):
        raise ValueError("ARM publication requires a live worker claim")
    existing_id = session.scalar(
        select(RuleSet.rule_set_id).where(RuleSet.job_id == job_id)
    )
    if existing_id is not None:
        return str(existing_id)
    # External heartbeats wait on our lock; start with a full publication lease.
    if repository.heartbeat(
        job_id, claim_token, lease_seconds=settings.job_lease_seconds
    ) is None:
        raise ValueError("ARM publication requires a live worker claim")

    # Retry on version collision: two concurrent ARM jobs may compute the
    # same max(version) + 1; the UNIQUE constraint catches it.
    last_collision: IntegrityError | None = None
    for _attempt in range(3):
        highest_version = session.scalar(select(func.max(RuleSet.version))) or 0
        savepoint = session.begin_nested()
        rule_set = RuleSet(
            rule_set_id=uuid4(),
            version=highest_version + 1,
            status="draft",
            notes=notes,
            job_id=job_id,
            source_job_id=job_id,
            requested_by_user_id=requested_by_user_id,
        )
        session.add(rule_set)
        try:
            session.flush()
            break
        except IntegrityError as exc:
            # Roll back only to the savepoint; session.rollback() would close
            # the caller's transaction context and abort the whole write.
            savepoint.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint != "uq_rule_set_version":
                raise
            last_collision = exc
    else:
        raise RuntimeError(
            "failed to assign RuleSet version after retries"
        ) from last_collision

    roster = dict(session.execute(select(Faculty.faculty_id, Faculty.full_name)).tuples().all())
    known_faculty = set(roster)
    known_subjects = set(session.scalars(select(Subject.subject_code)).all())
    known_rooms = set(session.scalars(select(Room.room_code)).all())

    stream_summaries: list[JsonObject] = []
    persisted_pairs: set[tuple[str, UUID]] = set()
    for stream_result in candidate.streams:
        excluded = {"unknown_faculty": 0, "unknown_subject": 0, "unknown_room": 0}
        persisted = 0
        session.add(
            RuleSetThreshold(
                rule_set_id=rule_set.rule_set_id,
                pattern_type=str(stream_result.stream),
                min_support=stream_result.thresholds.min_support,
                min_confidence=stream_result.thresholds.min_confidence,
                min_lift=stream_result.thresholds.min_lift,
                min_term_recurrence=stream_result.thresholds.min_term_recurrence,
                earliest_academic_year=policy.earliest_academic_year(
                    stream_result.stream
                ),
            )
        )
        for rule in stream_result.rules:
            if rule.faculty_id not in known_faculty:
                excluded["unknown_faculty"] += 1
                continue
            if (
                rule.subject_code is not None
                and rule.subject_code not in known_subjects
            ):
                excluded["unknown_subject"] += 1
                continue
            if rule.room_code is not None and rule.room_code not in known_rooms:
                excluded["unknown_room"] += 1
                continue
            session.add(
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
        stream_summaries.append(
            {
                "stream": stream_result.stream.value,
                "training_term_count": stream_result.training_term_count,
                "transaction_count": stream_result.transaction_count,
                "mined_rule_count": len(stream_result.rules),
                "persisted_rule_count": persisted,
                "excluded_rule_counts": excluded,
                "skipped_reason": (
                    stream_result.skipped_reason.value
                    if stream_result.skipped_reason is not None
                    else None
                ),
            }
        )
    persisted_total = sum(item["persisted_rule_count"] for item in stream_summaries)
    if persisted_total == 0:
        raise ValueError("catalog filtering left no usable rules")
    from app.services.arm.faculty_metric_rows import (
        faculty_metric_rows,
        version_faculty_metrics,
    )

    # The payload already carries only the terms the administrator selected, so
    # the mined terms below are the eligible part of that selection.
    prepared = prepare_training_data(
        historical_terms, policy, mode=MiningMode.OPERATIONAL
    )

    session.add_all(
        faculty_metric_rows(
            rule_set.rule_set_id,
            version_faculty_metrics(prepared, candidate),
            persisted=persisted_pairs,
            roster=roster,
        )
    )

    snapshot_ids = {term.snapshot_id for data in prepared for term in data.mined_terms}
    rule_set.source_snapshots.extend(
        session.scalars(
            select(HistoricalSnapshot).where(
                HistoricalSnapshot.snapshot_id.in_(snapshot_ids)
            )
        ).all()
    )
    source_term_ids = set(
        session.scalars(
            select(HistoricalSnapshot.source_term_id).where(
                HistoricalSnapshot.snapshot_id.in_(snapshot_ids)
            )
        ).all()
    )
    included_terms = session.scalars(
        select(Term).where(Term.term_id.in_(source_term_ids))
    ).all()
    rule_set.included_terms.extend(included_terms)
    summary = {
        "input_record_count": sum(len(term.offerings) for term in historical_terms),
        "mined_rule_count": len(candidate.rules),
        "persisted_rule_count": persisted_total,
        "streams": stream_summaries,
    }
    rule_set.mining_summary = summary

    if result is not None:
        persisted_by_stream = {item["stream"]: item for item in summary["streams"]}
        rule_set.publication_result = {
            **result,
            "rule_set_id": str(rule_set.rule_set_id),
            "metrics": {
                **result["metrics"],
                "input_record_count": summary["input_record_count"],
                "mined_rule_count": summary["mined_rule_count"],
                "retained_rule_count": summary["persisted_rule_count"],
            },
            "streams": [
                {
                    **item,
                    "retained_rule_count": persisted_by_stream[item["stream"]][
                        "persisted_rule_count"
                    ],
                    "excluded_rule_counts": persisted_by_stream[item["stream"]][
                        "excluded_rule_counts"
                    ],
                }
                for item in result["streams"]
            ],
        }
    from app.services.audit import record_event

    record_event(
        session,
        actor_id=requested_by_user_id,
        operation="rule_set.draft_publish",
        target_type="rule_set",
        target_id=rule_set.rule_set_id,
        details={
            "version": rule_set.version,
            "job_id": job_id,
            "status": "draft",
            "input_record_count": summary["input_record_count"],
            "mined_rule_count": summary["mined_rule_count"],
            "persisted_rule_count": summary["persisted_rule_count"],
        },
    )
    session.flush()
    # The job row stays locked, but the lease can expire while writes are flushed.
    if not repository.lock_live_claim(job_id, claim_token, job_type=JobType.ARM):
        raise ValueError("ARM publication requires a live worker claim")
    return str(rule_set.rule_set_id)


def _optional_uuid(value: Any) -> UUID | None:
    return UUID(value) if value is not None else None


def run_arm(payload: Mapping[str, Any]) -> JsonObject:
    require_version(payload)
    job_id = UUID(payload["job_id"])
    claim_token = UUID(payload["claim_token"])
    from sqlalchemy import select
    from sqlalchemy.orm import undefer

    from app.db.session import SessionLocal
    from app.models import JobType, RuleSet
    from app.repositories.jobs import JobRepository

    # Recover the committed artifact/result even if a later miner would fail.
    with SessionLocal.begin() as session:
        if not JobRepository(session).lock_live_claim(
            job_id, claim_token, job_type=JobType.ARM
        ):
            raise ValueError("ARM publication requires a live worker claim")
        published = session.scalar(
            select(RuleSet)
            .options(undefer(RuleSet.publication_result))
            .where(RuleSet.job_id == job_id)
        )
        if published is not None:
            result = published.publication_result
            if (
                not isinstance(result, dict)
                or result.get("rule_set_id") != str(published.rule_set_id)
            ):
                raise ValueError("ARM publication result is unavailable")
            return result
    terms = decode_historical_terms(payload["terms"])
    thresholds = decode_mining_thresholds(payload["thresholds"])
    candidate = RuleMiner().mine(
        prepare_training_data(terms, mode=MiningMode.OPERATIONAL), thresholds
    )
    if candidate.is_empty:
        raise JobDomainError("arm_no_rules_retained")

    result = {
        "version": CODEC_VERSION,
        "metrics": {
            "stream_count": len(candidate.streams),
            "input_record_count": sum(len(term.offerings) for term in terms),
            "mined_rule_count": len(candidate.rules),
            "is_empty": candidate.is_empty,
        },
        "streams": [
            {
                "stream": result.stream.value,
                "thresholds": {
                    "min_support": str(result.thresholds.min_support),
                    "min_confidence": str(result.thresholds.min_confidence),
                    "min_lift": str(result.thresholds.min_lift),
                    "min_term_recurrence": result.thresholds.min_term_recurrence,
                },
                "training_term_count": result.training_term_count,
                "transaction_count": result.transaction_count,
                "mined_rule_count": len(result.rules),
                "skipped_reason": (
                    result.skipped_reason.value
                    if result.skipped_reason is not None
                    else None
                ),
                "rules": [
                    {
                        "stream": rule.stream.value,
                        "faculty_id": str(rule.faculty_id),
                        "subject_code": rule.subject_code,
                        "room_code": rule.room_code,
                        "meeting_days": rule.meeting_days,
                        "time_slot": rule.time_slot,
                        "support": str(rule.support),
                        "confidence": str(rule.confidence),
                        "lift": str(rule.lift),
                        "term_recurrence": rule.term_recurrence,
                        "guidance_score": str(rule.guidance_score),
                    }
                    for rule in result.rules
                ],
            }
            for result in candidate.streams
        ],
    }
    with SessionLocal.begin() as session:
        _persist_draft_ruleset(
            session, candidate, terms, notes=payload.get("notes"),
            job_id=job_id,
            claim_token=claim_token,
            result=result,
            requested_by_user_id=_optional_uuid(payload.get("requested_by")),
        )
        return session.scalar(
            select(RuleSet.publication_result).where(RuleSet.job_id == job_id)
        )


def run_evaluation(payload: Mapping[str, Any]) -> JsonObject:
    """CHR-115: ten matched guided/unguided pairs over one snapshotted input.

    Every run shares the payload's problem and config and differs only by seed
    and by whether the snapshotted Rule Set guides fitness. Both modes'
    outcomes are scored with that Rule Set, and failed runs are kept.
    """
    from app.services.evaluation import (
        EvaluationMode,
        EvaluationStatus,
        begin_evaluation,
        checkpoint_evaluation_run,
        run_matched_evaluation,
    )
    import os
    from uuid import uuid4

    require_version(payload)
    checkpoint = None
    if os.environ.get("GA_SKIP_PERSIST"):
        # Preserve the existing explicitly opted-in database-free test path.
        batch_id = uuid4()
    else:
        from app.db.session import SessionLocal

        job_id = UUID(payload["job_id"])
        claim_token = UUID(payload["claim_token"])
        with SessionLocal() as session:
            batch_id, payload = begin_evaluation(session, job_id, claim_token)

        def checkpoint(record):
            with SessionLocal() as session:
                checkpoint_evaluation_run(session, job_id, claim_token, batch_id, record)

    problem = decode_scheduling_problem(payload["problem"])
    guidance = decode_guidance_snapshot(payload)
    if guidance is None:
        raise ValueError("matched evaluation requires a Rule Set snapshot")
    guided_scorer = RuleBackedHistoricalScorer(guidance)

    def runner(mode, config):
        scorer = (
            guided_scorer
            if mode is EvaluationMode.GUIDED
            else ZeroHistoricalAttainmentScorer()
        )
        return GAEngine(problem, config, historical_scorer=scorer).run()

    evaluation = run_matched_evaluation(
        problem,
        payload["input_fingerprint"],
        decode_ga_config(payload["config"]),
        payload["seeds"],
        runner,
        lambda candidate: guided_scorer.score(problem, candidate.assignments),
        checkpoint=checkpoint,
    )

    succeeded = sum(
        run.status is EvaluationStatus.SUCCEEDED for run in evaluation.runs
    )
    return {
        "version": CODEC_VERSION,
        "evaluation_batch_id": str(batch_id),
        "rule_set_id": str(guidance.rule_set_id),
        "rule_set_version": guidance.version,
        "input_fingerprint": evaluation.input_fingerprint,
        "succeeded_run_count": succeeded,
        "failed_run_count": len(evaluation.runs) - succeeded,
    }


Handler = Callable[[Mapping[str, Any]], JsonObject]
HANDLERS: dict[str, Handler] = {
    "ga": run_ga,
    "arm": run_arm,
    "evaluation": run_evaluation,
}


def dispatch(kind: str, payload: Mapping[str, Any]) -> JsonObject:
    try:
        handler = HANDLERS[kind]
    except KeyError:
        raise ValueError("unsupported job kind") from None
    return handler(payload)
