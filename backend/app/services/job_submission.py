"""CHR-31 / CHR-WORKER-001 snapshot-and-enqueue application service."""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain import BlockGenerationContext, LockedAssignment
from app.models import ExecutionJob, Faculty, RuleSet
from app.models.execution_job import JobStatus, JobType
from app.repositories.scheduling import (
    SQLAlchemyBlockGenerationContextProvider,
    SQLAlchemySchedulingProblemProvider,
)
from app.schemas.jobs import SubmitARMJobRequest, SubmitGAJobRequest
from app.services.arm.mining import MiningThresholds
from app.services.arm.preprocessing import (
    HistoricalInclusionPolicy,
    HistoricalTerm,
    MiningMode,
    PatternStream,
    prepare_training_data,
)
from app.services.arm.providers import DatabaseRuleProvider
from app.services.arm.service import load_historical_terms_by_ids
from app.services.audit import record_event
from app.services.ga.config import GAConfig, validate_meeting_day_patterns
from app.services.ga_configuration import active_ga_config
from app.services.jobs.codecs import (
    decode_block_generation_context,
    encode_guidance_snapshot,
)
from app.services.readiness import ReadinessReport, analyze_readiness

logger = logging.getLogger(__name__)


class HistoricalSelectionError(ValueError):
    """The selected history cannot produce a draft rule set.

    Raised before anything is enqueued so the administrator gets an answer they
    can act on, rather than a job that fails in the worker.
    """

    def __init__(self, message: str, snapshot_ids: tuple[UUID, ...]) -> None:
        super().__init__(message)
        self.message = message
        self.snapshot_ids = snapshot_ids


class GAGuidanceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class GAContextJobError(ValueError):
    """A context run named by a generate-all batch cannot be used as context."""

    def __init__(self, message: str, job_id: UUID):
        self.job_id = job_id
        super().__init__(message)


def add_proposal_locks(
    session: Session,
    context: BlockGenerationContext,
    problem: Any,
    context_job_ids: list[UUID],
    context_edits: Mapping[UUID, Sequence[Mapping[str, Any]]] | None = None,
) -> BlockGenerationContext:
    """Hold other Blocks' unreviewed proposals as extra locked context.

    A generate-all batch runs Blocks one after another before any is accepted.
    Each run's candidate is passed to the next run as immutable context, exactly
    like an accepted assignment, so the batch's proposals do not double-book an
    instructor or room among themselves. Accepted assignments of the base
    schedule always win: a class already locked there is never locked twice.
    Nothing is persisted; the proposals still need review and acceptance.

    A proposal edited by hand in review is held as edited (``context_edits``,
    by job): holding the run's original candidate instead would leave the
    room, teacher or time a class was moved to looking free, and the next
    Block could be generated into it.
    """
    if not context_job_ids:
        return context
    classes = {section.class_id: section for section in problem.classes}
    units = {subject.subject_code: subject.units for subject in context.subjects}
    slot_ids = {slot.time_slot_id for slot in context.time_slots}
    locked_class_ids = {lock.class_id for lock in context.locked_assignments}
    extra: list[LockedAssignment] = []
    for job_id in context_job_ids:
        job = session.get(ExecutionJob, job_id)
        if job is None:
            raise GAContextJobError("A context run was not found.", job_id)
        if job.job_type != JobType.GA or job.status != JobStatus.SUCCEEDED:
            raise GAContextJobError(
                "A context run is not a completed generation run.", job_id
            )
        block_context = (
            job.request.get("block_context") if isinstance(job.request, dict) else None
        )
        try:
            job_context = decode_block_generation_context(block_context)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise GAContextJobError(
                "A context run is not a per-Block generation run.", job_id
            ) from None
        if (
            job_context.term.term_id != context.term.term_id
            or job_context.base_schedule_id != context.base_schedule_id
            or job_context.selected_block_id == context.selected_block_id
        ):
            raise GAContextJobError(
                "A context run was not generated for another Block of this "
                "term and base schedule.",
                job_id,
            )
        candidate = job.result.get("candidate") if isinstance(job.result, dict) else None
        rows = candidate.get("assignments") if isinstance(candidate, dict) else None
        if context_edits and job_id in context_edits:
            rows = [dict(row) for row in context_edits[job_id]]
        if not isinstance(rows, list):
            raise GAContextJobError("A context run has no candidate result.", job_id)
        for row in rows:
            try:
                class_id = UUID(row["class_id"])
                faculty_id = UUID(row["faculty_id"])
                time_slot_id = UUID(row["time_slot_id"])
                room_code = row.get("room_code") or ""
                meeting_days = row.get("meeting_days") or ""
            except (KeyError, TypeError, ValueError, AttributeError):
                raise GAContextJobError(
                    "A context run's candidate is malformed.", job_id
                ) from None
            section = classes.get(class_id)
            # A class removed since that run, or one of another Block's, adds
            # nothing; one already accepted is held by the base schedule.
            if (
                section is None
                or section.block_id != job_context.selected_block_id
                or class_id in locked_class_ids
                or time_slot_id not in slot_ids
                or not meeting_days
            ):
                continue
            locked_class_ids.add(class_id)
            extra.append(
                LockedAssignment(
                    class_id=class_id,
                    faculty_id=faculty_id,
                    time_slot_id=time_slot_id,
                    room_code=room_code,
                    meeting_days=meeting_days,
                    block_id=section.block_id,
                    offering_id=section.offering_id,
                    units=units.get(section.subject_code, 0),
                )
            )
    return replace(
        context, locked_assignments=tuple(context.locked_assignments) + tuple(extra)
    )


class GATermNotReadyError(ValueError):
    def __init__(self, report: ReadinessReport):
        self.report = report
        super().__init__("The term is not ready for schedule generation.")


def _reject_unknown_snapshots(
    selected_snapshot_ids: list[UUID],
    loaded: tuple[HistoricalTerm, ...],
) -> None:
    """An ID matching no snapshot would be dropped in silence, mining less
    history than the administrator selected."""
    found = {term.snapshot_id for term in loaded}
    missing = tuple(
        snapshot_id for snapshot_id in selected_snapshot_ids if snapshot_id not in found
    )
    if missing:
        raise HistoricalSelectionError(
            "One or more selected historical terms do not exist.", missing
        )


def _reject_unparsable_academic_years(terms: tuple[HistoricalTerm, ...]) -> None:
    """A stored academic year the inclusion policy cannot read is unusable.

    The policy raises a bare ``ValueError`` the moment it partitions such a term,
    so the selection is refused here as something the administrator can act on
    rather than escaping the request as a server error.
    """
    unparsable = tuple(
        term.snapshot_id
        for term in terms
        if not HistoricalInclusionPolicy.is_parsable_academic_year(term.academic_year)
    )
    if unparsable:
        raise HistoricalSelectionError(
            "One or more selected historical terms have a malformed academic "
            "year and cannot be mined.",
            unparsable,
        )


UNUSABLE_SELECTION_MESSAGE = (
    "The selected historical terms cannot be prepared for mining. Review the "
    "selection for duplicated terms or records and try again."
)


@contextmanager
def _refusal_about(snapshot_ids: tuple[UUID, ...]) -> Iterator[None]:
    """Report a bare ``ValueError`` about the selection as a selection error.

    Reading and partitioning the selected history validates it as it goes — a
    field left blank at capture, a duplicated record, an unreadable period — and
    signals every one of those with a plain ``ValueError``. Each is an answer
    about the request, so it is converted here rather than at the route, where
    the same catch would also swallow an unrelated defect and report it as the
    caller's fault. The message returned is fixed; the specific cause is logged,
    so nothing internal is echoed back.
    """
    try:
        yield
    except HistoricalSelectionError:
        raise
    except ValueError as error:
        logger.warning(
            "Rejected an ARM selection that could not be prepared: %s", error
        )
        raise HistoricalSelectionError(
            UNUSABLE_SELECTION_MESSAGE, snapshot_ids
        ) from error


def _reject_ineligible_selection(terms: tuple[HistoricalTerm, ...]) -> None:
    """A term older than every stream's window contributes to no stream.

    Each stream mines only from its own earliest academic year onward, so a
    well-formed but very old term is dropped by every partition. Mixed in with
    eligible terms it would be accepted and then silently omitted from the whole
    run, which is exactly the narrowing the eligibility listing exists to
    prevent. It is refused here, naming the terms that cannot be used.
    """
    policy = HistoricalInclusionPolicy()
    unusable = tuple(
        term.snapshot_id
        for term in terms
        if not any(
            policy.is_term_eligible(stream, term.academic_year, term.term_number)
            for stream in PatternStream
        )
    )
    if unusable:
        raise HistoricalSelectionError(
            "One or more selected historical terms are older than every pattern "
            "stream's earliest academic year, or are a summer term before "
            "AY 2023-2024, and cannot be mined.",
            unusable,
        )


def _reject_untrainable_selection(terms: tuple[HistoricalTerm, ...]) -> None:
    """Reject selections whose eligible terms form no mineable transactions.

    Operational mining uses every eligible term, so after
    ``_reject_ineligible_selection`` the only way to be untrainable is history
    with no usable records, such as room-less records alone.
    """
    prepared = prepare_training_data(terms, mode=MiningMode.OPERATIONAL)
    if any(data.is_trainable for data in prepared):
        return
    message = (
        "The selected historical terms contain no mineable historical records "
        "after eligibility processing."
    )
    raise HistoricalSelectionError(
        message,
        tuple(term.snapshot_id for term in terms),
    )


class JobRepository(Protocol):
    def enqueue(
        self,
        *,
        job_type: str,
        requested_by: UUID,
        request: dict[str, Any],
        timeout_seconds: int,
        max_attempts: int,
    ) -> Any: ...


class JobSubmissionService:
    """Captures immutable inputs in the request transaction, but runs no algorithm."""

    def __init__(
        self,
        session: Session,
        repository: JobRepository,
        ga_payload_encoder: Callable[[Any, GAConfig], dict[str, Any]],
        arm_payload_encoder: Callable[[Any, Any], dict[str, Any]],
        block_ga_payload_encoder: Callable[[Any, GAConfig], dict[str, Any]],
        *,
        max_attempts: int,
        ga_timeout_seconds: int,
        arm_timeout_seconds: int,
    ) -> None:
        self._session = session
        self._repository = repository
        self._ga_payload_encoder = ga_payload_encoder
        self._arm_payload_encoder = arm_payload_encoder
        self._block_ga_payload_encoder = block_ga_payload_encoder
        self._max_attempts = max_attempts
        self._ga_timeout_seconds = ga_timeout_seconds
        self._arm_timeout_seconds = arm_timeout_seconds

    def submit_ga(
        self,
        request: SubmitGAJobRequest,
        submitted_by_id: UUID,
        *,
        use_server_config: bool = False,
    ) -> Any:
        # The active saved configuration is copied into the payload here, so a
        # later save does not change a queued run.
        if use_server_config or request.config is None:
            config = active_ga_config(self._session)
        else:
            config = GAConfig(**request.config.model_dump())
        # CHR-168: a saved configuration from before CHR-125 may still list a
        # Sunday pattern; refuse it here rather than after a full GA run.
        try:
            validate_meeting_day_patterns(config.meeting_day_patterns)
        except ValueError as error:
            raise GAGuidanceError("INVALID_GA_CONFIGURATION", str(error)) from error

        # Share-lock every faculty row before reading masks. An availability
        # save then waits for this commit, so the masks frozen into the payload
        # are the ones readiness checked.
        self._session.scalars(
            select(Faculty.faculty_id)
            .order_by(Faculty.faculty_id)
            .with_for_update(read=True)
        ).all()
        problem = SQLAlchemySchedulingProblemProvider(self._session).get_problem(
            request.term_id
        )
        if request.block_id is not None:
            context = SQLAlchemyBlockGenerationContextProvider(
                self._session
            ).get_block_generation_context(
                request.term_id,
                request.block_id,
                base_schedule_id=request.base_schedule_id,
            )
            context = add_proposal_locks(
                self._session,
                context,
                problem,
                request.context_job_ids,
                {
                    item.job_id: [
                        row.model_dump(mode="json") for row in item.assignments
                    ]
                    for item in request.context_edits
                },
            )

        report = analyze_readiness(problem)
        if not report.ready:
            raise GATermNotReadyError(report)

        if request.block_id is not None:
            payload = self._block_ga_payload_encoder(context, config)
        else:
            payload = self._ga_payload_encoder(problem, config)

        active_statement = select(RuleSet.rule_set_id).where(RuleSet.status == "active")
        for _attempt in range(3):
            active_id = self._session.scalar(
                active_statement.with_for_update(read=True)
            )
            if active_id is not None or self._session.scalar(active_statement) is None:
                break
            # An activation that owned the first lock may have superseded its
            # row before PostgreSQL rechecked the predicate. Retry a fresh view.
        else:
            raise GAGuidanceError(
                "GUIDANCE_CHANGED",
                "Historical guidance changed during submission; refresh and retry.",
            )
        try:
            guidance = (
                DatabaseRuleProvider(self._session).get_rules(active_id)
                if active_id is not None
                else None
            )
            payload["historical_guidance"] = encode_guidance_snapshot(guidance)
        except ValueError as error:
            raise GAGuidanceError(
                "RULE_SET_INVALID",
                "Active historical guidance is invalid; an administrator must review it before generation.",
            ) from error
        job = self._repository.enqueue(
            job_type="ga",
            requested_by=submitted_by_id,
            request=payload,
            timeout_seconds=self._ga_timeout_seconds,
            max_attempts=self._max_attempts,
        )
        stored_payload = {**payload, "job_id": str(job.job_id)}
        self._session.execute(
            update(ExecutionJob)
            .where(ExecutionJob.job_id == job.job_id)
            .values(request=stored_payload)
        )
        self._session.commit()
        return job

    def submit_arm(self, request: SubmitARMJobRequest, submitted_by_id: UUID) -> Any:
        thresholds = {
            stream: MiningThresholds(**values.model_dump())
            for stream, values in (
                (PatternStream.TEACHER_SUBJECT, request.thresholds.teacher_subject),
                (PatternStream.TEACHER_DAY, request.thresholds.teacher_day),
                (PatternStream.TEACHER_TIME, request.thresholds.teacher_time),
                (
                    PatternStream.TEACHER_SUBJECT_ROOM,
                    request.thresholds.teacher_subject_room,
                ),
            )
        }
        # Only reading and validating the selection may answer for the request.
        # Encoding the payload and enqueueing the job are outside this block, so
        # a defect there stays a server error instead of being reported as bad
        # input.
        with _refusal_about(tuple(request.selected_snapshot_ids)):
            historical_terms = load_historical_terms_by_ids(
                self._session, request.selected_snapshot_ids
            )
            _reject_unknown_snapshots(request.selected_snapshot_ids, historical_terms)
            _reject_unparsable_academic_years(historical_terms)
            _reject_ineligible_selection(historical_terms)
            _reject_untrainable_selection(historical_terms)
        snapshot = self._arm_payload_encoder(
            historical_terms,
            thresholds,
            selected_snapshot_ids=request.selected_snapshot_ids,
        )
        if request.notes is not None:
            snapshot["notes"] = request.notes
        job = self._repository.enqueue(
            job_type="arm",
            requested_by=submitted_by_id,
            request=snapshot,
            timeout_seconds=self._arm_timeout_seconds,
            max_attempts=self._max_attempts,
        )
        # The draft records which run and administrator produced it, so both
        # travel with the frozen payload the worker mines.
        self._session.execute(
            update(ExecutionJob)
            .where(ExecutionJob.job_id == job.job_id)
            .values(
                request={
                    **snapshot,
                    "job_id": str(job.job_id),
                    "requested_by": str(submitted_by_id),
                }
            )
        )
        record_event(
            self._session,
            actor_id=submitted_by_id,
            operation="rule_set.mining_request",
            target_type="execution_job",
            target_id=job.job_id,
            details={
                "selected_snapshot_ids": request.selected_snapshot_ids,
                "thresholds": request.thresholds.model_dump(),
                "notes": request.notes,
            },
        )
        self._session.commit()
        return job
