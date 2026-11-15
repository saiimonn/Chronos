"""Matched GA evaluation; CHR-134 / CHR-EVAL-011 evidence safeguards."""

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json
import logging
from math import isclose, isfinite
from string import hexdigits
from time import perf_counter
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.errors import APIError
from app.domain import CandidateSchedule, ScheduleValidationResult, SchedulingProblem
from app.models.evaluation import EvaluationBatch, EvaluationRun
from app.services.ga import GAConfig, GARunResult, HistoricalScoreBreakdown
from app.services.schedule_export import build_csv, build_xlsx
from app.services.validation import ScheduleValidator


logger = logging.getLogger(__name__)

MATCHED_PAIR_COUNT = 10
SEED_MIN = -(2**63)
SEED_MAX = 2**63 - 1
EVALUATION_EXPORT_HEADERS = (
    "pair_index",
    "mode",
    "random_seed",
    "status",
    "termination_reason",
    "termination_generation",
    "elapsed_seconds",
    "required_section_count",
    "assigned_section_count",
    "assignment_completeness",
    "hard_conflict_count",
    "historical_pattern_attainment",
    "historical_score_breakdown",
    "generation_metrics",
    "error_message",
    "input_fingerprint",
    "config",
    "execution_state",
    "run_total_seconds",
)


class EvaluationMode(StrEnum):
    GUIDED = "guided"
    UNGUIDED = "unguided"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EvaluationRunRecord:
    pair_index: int
    mode: EvaluationMode
    random_seed: int
    status: EvaluationStatus
    result: GARunResult | None = None
    historical_score_breakdown: HistoricalScoreBreakdown | None = None
    error_message: str | None = None
    validation_report: ScheduleValidationResult | None = None
    # CHR-137 / CHR-EVAL-013: setup, GA, independent validation and outcome
    # scoring for this run, unlike the engine-only elapsed_seconds.
    run_total_seconds: float | None = None

    @property
    def historical_pattern_attainment(self) -> float | None:
        return (
            self.historical_score_breakdown.historical_pattern_attainment
            if self.historical_score_breakdown is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class EvaluationBatchResult:
    term_id: UUID
    input_fingerprint: str
    config: GAConfig
    runs: tuple[EvaluationRunRecord, ...]


EvaluationRunner = Callable[[EvaluationMode, GAConfig], GARunResult]
EvaluationOutcomeScorer = Callable[[CandidateSchedule], HistoricalScoreBreakdown]


def _validate_inputs(input_fingerprint: str, config: GAConfig, seeds: Sequence[int]) -> None:
    if len(seeds) != MATCHED_PAIR_COUNT:
        raise ValueError(f"exactly {MATCHED_PAIR_COUNT} seeds are required")
    if len(set(seeds)) != MATCHED_PAIR_COUNT:
        raise ValueError("matched evaluation seeds must be unique")
    if any(
        type(seed) is not int or not SEED_MIN <= seed <= SEED_MAX
        for seed in (*seeds, config.random_seed)
    ):
        raise ValueError(
            f"matched evaluation seeds must fit a 64-bit integer ({SEED_MIN}..{SEED_MAX})"
        )
    if len(input_fingerprint) != 64 or any(
        character not in hexdigits for character in input_fingerprint
    ):
        raise ValueError("input_fingerprint must be a SHA-256 hex digest")


def run_matched_evaluation(
    problem: SchedulingProblem,
    input_fingerprint: str,
    config: GAConfig,
    seeds: Sequence[int],
    runner: EvaluationRunner,
    outcome_scorer: EvaluationOutcomeScorer,
    *,
    checkpoint: Callable[[EvaluationRunRecord], None] | None = None,
) -> EvaluationBatchResult:
    """Run ten pairs; freshly validate and score both modes' selected outcomes."""
    _validate_inputs(input_fingerprint, config, seeds)
    records = []
    validator = ScheduleValidator()
    offerings = {item.offering_id for item in problem.classes}
    classes = {item.class_id: item for item in problem.classes}
    for pair_index, seed in enumerate(seeds):
        run_config = replace(config, random_seed=seed)
        for mode in EvaluationMode:
            if checkpoint is not None:
                checkpoint(EvaluationRunRecord(
                    pair_index, mode, seed, EvaluationStatus.RUNNING,
                ))
            result = None
            validation = None
            started_at = perf_counter()
            try:
                result = runner(mode, run_config)
                validation = validator.validate(problem, result.candidate)
                assigned_offerings = {
                    classes[item.class_id].offering_id
                    for item in result.candidate.assignments
                    if item.room_code is not None
                }
                if (
                    result.metrics.required_class_count != validation.required_class_count
                    or result.metrics.assigned_class_count != validation.assigned_class_count
                    or result.metrics.hard_conflict_count != validation.hard_conflict_count
                    or result.metrics.required_offering_count != len(offerings)
                    or result.metrics.assigned_offering_count != len(assigned_offerings)
                ):
                    raise ValueError("optimizer counters disagree with independent validation")
                if result.metrics.random_seed != seed or result.metrics.config != run_config:
                    raise ValueError("runner returned metrics for a different seed or configuration")
                outcome_scores = outcome_scorer(result.candidate)
                outcome_attainment = float(
                    outcome_scores.historical_pattern_attainment
                )
                score_values = (
                    outcome_scores.teacher_subject_score,
                    outcome_scores.teacher_day_score,
                    outcome_scores.teacher_time_score,
                    outcome_scores.teacher_subject_room_score,
                    outcome_attainment,
                )
                if any(
                    value is not None and (not isfinite(value) or not 0 <= value <= 1)
                    for value in score_values
                ):
                    raise ValueError("outcome historical scores must be between 0 and 1")
                if mode is EvaluationMode.GUIDED and not isclose(
                    result.metrics.historical_pattern_attainment,
                    outcome_attainment,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        "guided fitness and post-run historical attainment do not match"
                    )
                if mode is EvaluationMode.UNGUIDED and any(
                    not isclose(metric.best_fitness[2], 0.0, abs_tol=1e-12)
                    for metric in result.metrics.generations
                ):
                    raise ValueError("unguided fitness must not use historical guidance")
            except Exception as error:
                logger.exception(
                    "matched evaluation run failed: pair_index=%s mode=%s random_seed=%s",
                    pair_index,
                    mode.value,
                    seed,
                )
                record = EvaluationRunRecord(
                    pair_index=pair_index, mode=mode, random_seed=seed,
                    status=EvaluationStatus.FAILED, result=result,
                    validation_report=validation,
                    error_message=str(error)[:500] or type(error).__name__,
                )
            else:
                record = EvaluationRunRecord(
                    pair_index=pair_index, mode=mode, random_seed=seed,
                    status=EvaluationStatus.SUCCEEDED, result=result,
                    validation_report=validation,
                    historical_score_breakdown=outcome_scores,
                    run_total_seconds=perf_counter() - started_at,
                )
            records.append(record)
            # A persistence outage must stop execution, not become an optimizer failure.
            if checkpoint is not None:
                checkpoint(record)
    return EvaluationBatchResult(
        problem.term.term_id, input_fingerprint, config, tuple(records),
    )


def begin_evaluation(db: Session, job_id: UUID, claim_token: UUID) -> tuple[UUID, dict]:
    """Create all planned slots once, under the actual worker claim."""
    from app.core.config import settings
    from app.models import JobType
    from app.repositories.jobs import JobRepository
    from app.services.jobs.codecs import decode_ga_config, require_version

    repository = JobRepository(db)
    try:
        if not repository.lock_live_claim(job_id, claim_token, job_type=JobType.EVALUATION):
            raise ValueError("Evaluation evidence requires a live worker claim")
        if db.scalar(select(EvaluationBatch.evaluation_batch_id).where(
            EvaluationBatch.job_id == job_id,
        )) is not None:
            raise ValueError("Evaluation already started for this job; do not rerun evidence")
        request = repository.get(job_id).request
        snapshot = {
            key: request[key] for key in (
                "version", "kind", "problem", "historical_guidance",
                "config", "seeds", "input_fingerprint",
            )
        }
        require_version(snapshot)
        config = decode_ga_config(snapshot["config"])
        _validate_inputs(snapshot["input_fingerprint"], config, snapshot["seeds"])
        if snapshot["input_fingerprint"] != input_fingerprint(
            snapshot["problem"], snapshot["historical_guidance"],
        ):
            raise ValueError("Evaluation input fingerprint does not match its snapshot")
        batch = EvaluationBatch(
            job_id=job_id, term_id=UUID(snapshot["problem"]["term"]["term_id"]),
            input_fingerprint=snapshot["input_fingerprint"], config=asdict(config),
            input_snapshot=snapshot,
            runs=[
                EvaluationRun(
                    pair_index=index, mode=mode.value, random_seed=seed,
                    status=EvaluationStatus.PENDING.value,
                )
                for index, seed in enumerate(snapshot["seeds"])
                for mode in EvaluationMode
            ],
        )
        db.add(batch)
        db.flush()
        batch_id = batch.evaluation_batch_id
        if repository.heartbeat(
            job_id, claim_token, lease_seconds=settings.job_lease_seconds,
            progress={
                "evaluation_batch_id": str(batch_id), "completed_run_count": 0,
                "planned_run_count": 2 * MATCHED_PAIR_COUNT,
            },
        ) is None:
            raise ValueError("Evaluation worker claim expired before checkpoint commit")
        db.commit()
        return batch_id, snapshot
    except Exception:
        db.rollback()
        raise


def checkpoint_evaluation_run(
    db: Session,
    job_id: UUID,
    claim_token: UUID,
    batch_id: UUID,
    record: EvaluationRunRecord,
) -> None:
    """Persist one monotonic run transition and its progress atomically."""
    from app.core.config import settings
    from app.models import JobType
    from app.repositories.jobs import JobRepository

    repository = JobRepository(db)
    try:
        if not repository.lock_live_claim(job_id, claim_token, job_type=JobType.EVALUATION):
            raise ValueError("Evaluation evidence requires a live worker claim")
        run = db.scalar(select(EvaluationRun).join(EvaluationBatch).where(
            EvaluationBatch.evaluation_batch_id == batch_id,
            EvaluationBatch.job_id == job_id,
            EvaluationRun.pair_index == record.pair_index,
            EvaluationRun.mode == record.mode.value,
        ))
        if run is None or run.random_seed != record.random_seed:
            raise ValueError("Evaluation slot or frozen seed does not match")
        if run.status in (EvaluationStatus.SUCCEEDED.value, EvaluationStatus.FAILED.value):
            raise ValueError("Evaluation run already finished; evidence cannot be overwritten")
        expected = (
            EvaluationStatus.PENDING
            if record.status is EvaluationStatus.RUNNING else EvaluationStatus.RUNNING
        )
        if run.status != expected.value or record.status is EvaluationStatus.PENDING:
            raise ValueError("Invalid evaluation checkpoint transition")
        for name, value in _run_values(record).items():
            setattr(run, name, value)
        db.flush()
        completed = db.scalar(select(func.count()).select_from(EvaluationRun).where(
            EvaluationRun.evaluation_batch_id == batch_id,
            EvaluationRun.status.in_((EvaluationStatus.SUCCEEDED.value, EvaluationStatus.FAILED.value)),
        ))
        if repository.heartbeat(
            job_id, claim_token, lease_seconds=settings.job_lease_seconds,
            progress={
                "evaluation_batch_id": str(batch_id), "completed_run_count": completed,
                "planned_run_count": 2 * MATCHED_PAIR_COUNT,
            },
        ) is None:
            raise ValueError("Evaluation worker claim expired before checkpoint commit")
        db.commit()
    except Exception:
        db.rollback()
        raise


class EvaluationGuidanceError(ValueError):
    """The selected Rule Set cannot be snapshotted as valid guidance."""


class EvaluationHoldoutLeakError(ValueError):
    """The Rule Set was mined from terms the evaluation must withhold."""

    def __init__(self, periods: Sequence[str]):
        super().__init__("rule set includes holdout terms: " + ", ".join(periods))
        self.periods = tuple(periods)


class EvaluationProvenanceUnknownError(ValueError):
    """The Rule Set's recorded terms are missing or unreadable, so a holdout
    leak cannot be ruled out."""


def ensure_rule_set_excludes_holdout(db: Session, rule_set_id: UUID) -> None:
    """Refuse a Rule Set that drew on the evaluation holdout (thesis p.52).

    The holdout is the latest complete academic year of the captured history;
    terms of newer years are refused with it, as evaluation-mode mining
    withholds both. Periods come from the
    immutable snapshots: linked terms are resolved to the snapshot captured
    from them, because a term's own year and number can still be edited. A
    term with no snapshot falls back to its current period; once a holdout
    exists, a Rule Set with no recorded terms, or one linking a term whose year
    can no longer be read, is refused as provenance unknown. Years the
    inclusion policy cannot parse cannot be mined and are left out, as job
    submission does. Without a holdout there is nothing to withhold.
    """
    from app.models import HistoricalSnapshot, RuleSet
    from app.services.arm.preprocessing import (
        HistoricalInclusionPolicy,
        HistoricalTerm,
        PatternStream,
    )

    snapshots = db.execute(
        select(
            HistoricalSnapshot.snapshot_id,
            HistoricalSnapshot.source_term_id,
            HistoricalSnapshot.academic_year,
            HistoricalSnapshot.term_number,
        )
    ).all()
    history = tuple(
        HistoricalTerm(row.snapshot_id, row.academic_year, row.term_number, ())
        for row in snapshots
        if HistoricalInclusionPolicy.is_parsable_academic_year(row.academic_year)
    )
    # Teacher-subject has the widest window (2018 on), so its partition sees
    # every captured term; the holdout year is the same for all four streams.
    partition = HistoricalInclusionPolicy().partition(
        history, PatternStream.TEACHER_SUBJECT
    )
    if not partition.holdout_terms:
        return
    rule_set = db.get(RuleSet, rule_set_id)
    if rule_set is None:
        return
    # Evaluation-mode mining also withholds every term newer than the holdout
    # year, since it would leak later history into training. Judging by year
    # rather than by captured period also covers linked terms with no snapshot.
    holdout_start = min(int(term.academic_year[:4]) for term in partition.holdout_terms)
    snapshot_period = {
        row.source_term_id: (row.academic_year, row.term_number) for row in snapshots
    }
    used = {
        snapshot_period.get(term.term_id, (term.academic_year, term.term_number))
        for term in rule_set.included_terms
    }
    used |= {
        (snapshot.academic_year, snapshot.term_number)
        for snapshot in rule_set.source_snapshots
    }
    if not used:
        raise EvaluationProvenanceUnknownError(
            "rule set records no included terms or source snapshots"
        )
    # Every period was parsable when it was linked, so an unreadable one is a
    # term edited since; its year can no longer show it predates the holdout.
    if any(not HistoricalInclusionPolicy.is_parsable_academic_year(year) for year, _ in used):
        raise EvaluationProvenanceUnknownError(
            "rule set links a term whose academic year cannot be read"
        )
    if leaked := sorted(
        f"{year} T{number}"
        for year, number in used
        if int(year[:4]) >= holdout_start
    ):
        raise EvaluationHoldoutLeakError(leaked)


def submit_matched_evaluation(
    db: Session,
    repository,
    *,
    term_id: UUID,
    rule_set_id: UUID,
    seeds: Sequence[int],
    config: GAConfig,
    requested_by: UUID,
    timeout_seconds: int,
):
    """Snapshot the term, Rule Set and config, then enqueue one worker job.

    Nothing runs here. The payload carries every input the twenty runs use, so
    a later catalog edit or Rule Set change cannot alter a queued evaluation.
    The job is not retried: interrupted experiments retain their own evidence
    rather than silently rerunning seeds. A new submission is a new execution.
    """
    from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
    from app.services.arm.providers import DatabaseRuleProvider
    from app.services.jobs.codecs import (
        CODEC_VERSION,
        encode_ga_config,
        encode_guidance_snapshot,
        encode_scheduling_problem,
    )

    problem = encode_scheduling_problem(
        SQLAlchemySchedulingProblemProvider(db).get_problem(term_id)
    )
    ensure_rule_set_excludes_holdout(db, rule_set_id)
    try:
        snapshot = encode_guidance_snapshot(
            DatabaseRuleProvider(db).get_rules(rule_set_id)
        )
    except ValueError as error:
        raise EvaluationGuidanceError(str(error)) from error
    payload = {
        "version": CODEC_VERSION,
        "kind": "evaluation",
        "problem": problem,
        "historical_guidance": snapshot,
        "config": encode_ga_config(config),
        "seeds": list(seeds),
        "input_fingerprint": input_fingerprint(problem, snapshot),
    }
    job = repository.enqueue(
        job_type="evaluation",
        requested_by=requested_by,
        request=payload,
        timeout_seconds=timeout_seconds,
        max_attempts=1,
    )
    db.commit()
    return job


def input_fingerprint(problem: dict, guidance_snapshot: dict) -> str:
    """SHA-256 of the canonical JSON of the shared scheduling input and guidance."""
    canonical = json.dumps(
        {"problem": problem, "historical_guidance": guidance_snapshot},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def persist_evaluation(db: Session, evaluation: EvaluationBatchResult) -> EvaluationBatch:
    """Store a standalone completed batch; production workers use checkpoints."""
    batch = EvaluationBatch(
        term_id=evaluation.term_id,
        input_fingerprint=evaluation.input_fingerprint,
        config=asdict(evaluation.config),
    )
    db.add(batch)
    for record in evaluation.runs:
        batch.runs.append(
            EvaluationRun(
                pair_index=record.pair_index,
                mode=record.mode.value,
                random_seed=record.random_seed,
                **_run_values(record),
            )
        )
    db.commit()
    return get_evaluation(db, batch.evaluation_batch_id)


def _run_values(record: EvaluationRunRecord) -> dict:
    metrics = (
        record.result.metrics
        if record.result is not None and record.status is EvaluationStatus.SUCCEEDED
        else None
    )
    validation = record.validation_report
    if metrics is not None and validation is None:
        raise ValueError("Successful evaluation outcomes require independent validation")
    return {
        "status": record.status.value,
        "termination_reason": metrics.termination_reason.value if metrics else None,
        "termination_generation": metrics.termination_generation if metrics else None,
        "elapsed_seconds": metrics.elapsed_seconds if metrics else None,
        "run_total_seconds": record.run_total_seconds if metrics else None,
        "required_section_count": validation.required_class_count if metrics else None,
        "assigned_section_count": validation.assigned_class_count if metrics else None,
        "hard_conflict_count": validation.hard_conflict_count if metrics else None,
        "historical_pattern_attainment": record.historical_pattern_attainment if metrics else None,
        "historical_score_breakdown": (
            asdict(record.historical_score_breakdown)
            if metrics and record.historical_score_breakdown is not None else None
        ),
        "generation_metrics": (
            [{
                "generation": item.generation,
                "best_fitness": list(item.best_fitness),
                "mean_assignment_completeness": item.mean_assignment_completeness,
            } for item in metrics.generations] if metrics else None
        ),
        "error_message": record.error_message,
        # Domain UUIDs are stored as strings in the JSON evidence.
        "candidate": (
            json.loads(json.dumps(asdict(record.result.candidate), default=str))
            if record.result is not None else None
        ),
        "validation_report": (
            json.loads(json.dumps(
                asdict(validation) | {
                    "hard_conflict_count": validation.hard_conflict_count,
                    "is_export_eligible": validation.is_export_eligible,
                },
                default=str,
            )) if validation is not None else None
        ),
    }


def get_evaluation(db: Session, evaluation_batch_id: UUID) -> EvaluationBatch:
    batch = db.scalar(
        select(EvaluationBatch)
        # A terminal job cannot checkpoint again; read that gate before its ledger.
        .options(joinedload(EvaluationBatch.job), selectinload(EvaluationBatch.runs))
        .execution_options(populate_existing=True)
        .where(EvaluationBatch.evaluation_batch_id == evaluation_batch_id)
    )
    if batch is None:
        raise APIError(404, "NOT_FOUND", "The requested evaluation batch was not found.")
    return batch


def export_evaluation(batch: EvaluationBatch, export_format: str = "csv") -> bytes:
    """Export successful and failed runs together; failures are research evidence."""
    if batch.job is not None and batch.job.status in ("queued", "running"):
        raise APIError(
            409, "EVALUATION_RUNNING",
            "Wait for the evaluation job to finish before exporting its evidence.",
        )
    ordered_runs = sorted(batch.runs, key=lambda item: (item.pair_index, item.mode))
    if export_format == "json":
        return json.dumps({
            "schema_version": 1,
            "evaluation_batch_id": str(batch.evaluation_batch_id),
            "job_id": str(batch.job_id) if batch.job_id is not None else None,
            "job_status": batch.job.status if batch.job is not None else None,
            "job_error_code": batch.job.error_code if batch.job is not None else None,
            "input_fingerprint": batch.input_fingerprint,
            "config": batch.config, "input_snapshot": batch.input_snapshot,
            "planned_run_count": len(ordered_runs),
            "completed_run_count": sum(run.status in ("succeeded", "failed") for run in ordered_runs),
            "runs": [{
                "pair_index": run.pair_index, "mode": run.mode,
                "random_seed": run.random_seed, "status": run.status,
                "termination_reason": run.termination_reason,
                "termination_generation": run.termination_generation,
                "elapsed_seconds": run.elapsed_seconds,
                "run_total_seconds": run.run_total_seconds,
                "required_class_count": run.required_section_count,
                "assigned_class_count": run.assigned_section_count,
                "hard_conflict_count": run.hard_conflict_count,
                "historical_pattern_attainment": run.historical_pattern_attainment,
                "historical_score_breakdown": run.historical_score_breakdown,
                "generation_metrics": run.generation_metrics,
                "error_message": run.error_message,
                "candidate": run.candidate, "validation_report": run.validation_report,
            } for run in ordered_runs],
        }, sort_keys=True, allow_nan=False).encode("utf-8")
    config = json.dumps(batch.config, sort_keys=True, separators=(",", ":"))
    rows = []
    for run in ordered_runs:
        unfinished = run.status in ("pending", "running")
        error = run.error_message
        if unfinished:
            error = (
                "Run not started before job terminated."
                if run.status == "pending" else "Run interrupted before outcome checkpoint."
            )
        completeness = (
            run.assigned_section_count / run.required_section_count
            if run.required_section_count
            else None
        )
        rows.append(
            (
                run.pair_index,
                run.mode,
                run.random_seed,
                "failed" if unfinished else run.status,
                run.termination_reason,
                run.termination_generation,
                run.elapsed_seconds,
                run.required_section_count,
                run.assigned_section_count,
                completeness,
                run.hard_conflict_count,
                run.historical_pattern_attainment,
                (
                    json.dumps(
                        run.historical_score_breakdown,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if run.historical_score_breakdown is not None
                    else None
                ),
                (
                    json.dumps(
                        run.generation_metrics,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if run.generation_metrics is not None
                    else None
                ),
                error,
                batch.input_fingerprint,
                config,
                run.status,
                run.run_total_seconds,
            )
        )
    if export_format == "csv":
        return build_csv(EVALUATION_EXPORT_HEADERS, rows)
    if export_format == "xlsx":
        return build_xlsx(EVALUATION_EXPORT_HEADERS, rows)
    raise ValueError("export_format must be csv, xlsx or json")
