"""CHR-31 / CHR-WORKER-001 non-blocking job endpoints."""

import json
import logging
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.core.config import settings
from app.core.errors import APIError
from app.models import AppUser, JobStatus, JobType
from app.models.app_user import UserRole
from app.services.audit import record_event
from app.repositories.protocols import (
    BaseScheduleNotFoundError,
    BlockGenerationContextNotFoundError,
    SchedulingProblemNotFoundError,
)
from app.schemas.jobs import (
    DeleteJobsRequest,
    DeleteJobsResponse,
    JobAcceptedResponse,
    JobGuidanceReference,
    JobHistoryItem,
    JobHistoryPage,
    JobScope,
    JobStatusResponse,
    SkippedJob,
    SubmitARMJobRequest,
    SubmitGAJobRequest,
)
from app.services.job_submission import (
    GAContextJobError,
    GAGuidanceError,
    GATermNotReadyError,
    HistoricalSelectionError,
    JobSubmissionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_job_repository(db: Annotated[Session, Depends(get_db)]) -> Any:
    from app.repositories.jobs import JobRepository

    return JobRepository(db)


def get_job_submission_service(
    db: Annotated[Session, Depends(get_db)],
    repository: Annotated[Any, Depends(get_job_repository)],
) -> JobSubmissionService:
    from app.services.jobs.codecs import (
        encode_arm_payload,
        encode_block_ga_payload,
        encode_ga_payload,
    )

    return JobSubmissionService(
        db,
        repository,
        encode_ga_payload,
        encode_arm_payload,
        encode_block_ga_payload,
        max_attempts=settings.job_max_attempts,
        ga_timeout_seconds=settings.job_ga_timeout_seconds,
        arm_timeout_seconds=settings.job_arm_timeout_seconds,
    )


def _accepted(job: Any) -> JobAcceptedResponse:
    return JobAcceptedResponse(
        job_id=job.job_id, kind=job.job_type.value, status=job.status.value
    )


# Both run the GA over a frozen problem, config and guidance snapshot.
_GA_RUN_TYPES = (JobType.GA, JobType.EVALUATION)


def _guidance(job: Any) -> JobGuidanceReference | None:
    if job.job_type not in _GA_RUN_TYPES or not isinstance(job.request, dict):
        return None
    from app.services.jobs.codecs import decode_guidance_snapshot

    # Frozen at submission, so a later activation or rollback cannot relabel
    # the run. The worker's own decoder decides what the run used: payloads
    # older than codec 7 ran unguided, and a snapshot it would refuse is
    # reported unknown rather than guessed.
    try:
        decoded = decode_guidance_snapshot(job.request or {})
    except ValueError:
        logger.warning("ga_job_guidance_unreadable job_id=%s", job.job_id)
        return None
    if decoded is None:
        return JobGuidanceReference(mode="unguided")
    return JobGuidanceReference(
        mode="guided", rule_set_id=decoded.rule_set_id, rule_set_version=decoded.version
    )


def _object(value: Any, key: str) -> dict:
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


def _scope(job: Any) -> JobScope | None:
    """CHR-174: only the term/Block identity, never the queued problem itself."""
    if job.job_type not in _GA_RUN_TYPES:
        return None
    context = _object(_object(job.request, "block_context"), "context")
    term = _object(context, "term") or _object(_object(job.request, "problem"), "term")
    if not term:
        return None
    try:
        return JobScope(
            term_id=term.get("term_id"),
            academic_year=term.get("academic_year"),
            # Codec v1-3 payloads stored it as `semester` (see codecs.py).
            term_number=term.get("term_number", term.get("semester")),
            block_id=context.get("selected_block_id"),
        )
    except ValidationError:
        logger.warning("ga_job_scope_unreadable job_id=%s", job.job_id)
        return None


def _config(job: Any) -> dict[str, Any] | None:
    """The frozen GA parameters (and evaluation seeds); never the problem."""
    if job.job_type not in _GA_RUN_TYPES:
        return None
    config = _object(job.request, "config")
    if not config:
        return None
    # Seeds are 64-bit; strings keep them exact past JavaScript's 2**53.
    snapshot = {**config, "random_seed": _exact(config.get("random_seed"))}
    if job.job_type is JobType.EVALUATION:
        seeds = job.request.get("seeds")
        snapshot["seeds"] = [_exact(seed) for seed in seeds] if isinstance(seeds, list) else None
    return snapshot


def _exact(value: Any) -> Any:
    return str(value) if type(value) is int else value


def _display_id(job: Any, scope: JobScope | None, guidance: JobGuidanceReference | None, iteration: int | None) -> str | None:
    """Readable run ID: YYTT + program/year/letter (ALL term-wide) + Vn + run."""
    if job.job_type not in _GA_RUN_TYPES or scope is None or iteration is None:
        return None
    year = (scope.academic_year or "")[:4]
    if not year.isdigit() or scope.term_number is None:
        return None
    if scope.block_id is None:
        cohort = "ALL"
    else:
        block = _object(_object(_object(job.request, "block_context"), "context"), "selected_block")
        program = str(block.get("program_code") or "")
        cohort = (
            f"{program.removeprefix('BS')}{block.get('year_level')}{block.get('section_letter')}".upper()
            if program and block.get("year_level") is not None and block.get("section_letter")
            else "BLK"
        )
    version = guidance.rule_set_version if guidance and guidance.rule_set_version is not None else 0
    prefix = "E" if job.job_type is JobType.EVALUATION else ""
    return f"{prefix}{year[2:]}{scope.term_number:02d}{cohort}V{version}{iteration:02d}"


def _history_item(job: Any, iteration: int | None = None) -> JobHistoryItem:
    error = None
    if job.error_code is not None:
        error = {"code": job.error_code, "message": job.error_message}
    guidance, scope = _guidance(job), _scope(job)
    return JobHistoryItem(
        job_id=job.job_id,
        kind=job.job_type.value,
        status=job.status.value,
        submitted_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.finished_at,
        error=error,
        guidance=guidance,
        scope=scope,
        display_id=_display_id(job, scope, guidance, iteration),
    )


def _status(job: Any, iteration: int | None = None) -> JobStatusResponse:
    evaluation_batch = None
    progress = job.progress or {}
    if (
        job.job_type is JobType.EVALUATION
        and job.status in (JobStatus.FAILED, JobStatus.TIMED_OUT)
        and "evaluation_batch_id" in progress
    ):
        evaluation_batch = {
            key: progress[key]
            for key in ("evaluation_batch_id", "completed_run_count", "planned_run_count")
        }
    return JobStatusResponse(
        **_history_item(job, iteration).model_dump(),
        result=job.result,
        evaluation_batch=evaluation_batch,
        config=_config(job),
    )


@router.post(
    "/ga",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_ga_job(
    request: SubmitGAJobRequest,
    response: Response,
    user: Annotated[AppUser, Depends(require_committee_member)],
    service: Annotated[JobSubmissionService, Depends(get_job_submission_service)],
) -> JobAcceptedResponse:
    is_administrator = user.role == UserRole.DEPARTMENT_ADMINISTRATOR
    if request.config is not None and not is_administrator:
        raise APIError(
            403,
            "GA_CONFIG_OVERRIDE_FORBIDDEN",
            "Only a Department Administrator can override the GA configuration; "
            "omit config to use the saved configuration.",
        )
    try:
        job = service.submit_ga(
            request, user.user_id, use_server_config=not is_administrator
        )
    except SchedulingProblemNotFoundError as error:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.") from error
    except BlockGenerationContextNotFoundError as error:
        raise APIError(
            404, "NOT_FOUND", "The requested block was not found."
        ) from error
    except BaseScheduleNotFoundError as error:
        raise APIError(
            404, "NOT_FOUND", "The referenced base schedule was not found."
        ) from error
    except GAGuidanceError as error:
        raise APIError(409, error.code, str(error)) from error
    except GAContextJobError as error:
        raise APIError(
            409, "INVALID_CONTEXT_JOB", str(error), {"job_id": str(error.job_id)}
        ) from error
    except GATermNotReadyError as error:
        raise APIError(409, "TERM_NOT_READY", str(error), asdict(error.report)) from error
    response.headers["Location"] = f"/api/v1/jobs/{job.job_id}"
    return _accepted(job)


async def parse_arm_job_request(
    request: Request, _body: SubmitARMJobRequest,
) -> SubmitARMJobRequest:
    # The typed body retains FastAPI's JSON checks and OpenAPI; reparse to avoid floats.
    try:
        value = json.loads(await request.body(), parse_float=Decimal)
        return SubmitARMJobRequest.model_validate(value)
    except InvalidOperation as error:
        raise RequestValidationError([
            {"loc": ("body",), "type": "decimal_parsing"},
        ]) from error
    except ValidationError as error:
        raise RequestValidationError([
            {**item, "loc": ("body", *item["loc"])} for item in error.errors()
        ]) from error


@router.post(
    "/arm",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_arm_job(
    request: Annotated[SubmitARMJobRequest, Depends(parse_arm_job_request)],
    response: Response,
    user: Annotated[AppUser, Depends(require_department_administrator)],
    service: Annotated[JobSubmissionService, Depends(get_job_submission_service)],
) -> JobAcceptedResponse:
    try:
        job = service.submit_arm(request, user.user_id)
    except HistoricalSelectionError as error:
        raise APIError(
            422,
            "VALIDATION_ERROR",
            error.message,
            {
                "selected_snapshot_ids": [
                    str(snapshot_id) for snapshot_id in error.snapshot_ids
                ]
            },
        ) from error
    response.headers["Location"] = f"/api/v1/jobs/{job.job_id}"
    return _accepted(job)


@router.get("", response_model=JobHistoryPage)
def list_jobs(
    user: Annotated[AppUser, Depends(require_committee_member)],
    repository: Annotated[Any, Depends(get_job_repository)],
    kind: Annotated[JobType | None, Query()] = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    term_id: Annotated[UUID | None, Query()] = None,
    # PostgreSQL text cannot hold NUL, and OFFSET is a bigint.
    q: Annotated[str | None, Query(max_length=100, pattern=r"^[^\x00]*$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
) -> JobHistoryPage:
    """CHR-174 / CHR-SCH-014: role-scoped GA/ARM job history browser.

    Administrators see every job; committee members see only their own non-ARM
    jobs, exactly what ``read_job`` would let them open.
    """
    is_admin = user.role == UserRole.DEPARTMENT_ADMINISTRATOR
    allowed = [item for item in JobType if is_admin or item is not JobType.ARM]
    if kind is not None:
        if kind not in allowed:
            raise APIError(
                403, "FORBIDDEN", "ARM results are restricted to administrators."
            )
        allowed = [kind]
    jobs, total = repository.list_history(
        requested_by=None if is_admin else user.user_id,
        job_types=allowed,
        status=job_status,
        term_id=term_id,
        search=q,
        limit=limit,
        offset=offset,
    )
    iterations = repository.iterations([job.job_id for job in jobs])
    return JobHistoryPage(
        items=[_history_item(job, iterations.get(job.job_id)) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/delete", response_model=DeleteJobsResponse)
def delete_jobs(
    request: DeleteJobsRequest,
    user: Annotated[AppUser, Depends(require_committee_member)],
    db: Annotated[Session, Depends(get_db)],
    repository: Annotated[Any, Depends(get_job_repository)],
) -> DeleteJobsResponse:
    """Delete finished jobs from history, one or many at once.

    The same scope as ``list_jobs``: administrators may delete any job,
    committee members only their own non-ARM jobs. Queued and running jobs,
    and jobs a schedule, rule set or evaluation batch still points at, are
    skipped and reported instead of failing the whole request.
    """
    is_admin = user.role == UserRole.DEPARTMENT_ADMINISTRATOR
    outcome = repository.delete_finished(
        request.job_ids,
        requested_by=None if is_admin else user.user_id,
        job_types=None if is_admin else [item for item in JobType if item is not JobType.ARM],
    )
    deleted = [job_id for job_id, result in outcome.items() if result == "deleted"]
    if deleted:
        record_event(
            db,
            actor_id=user.user_id,
            operation="job.delete",
            target_type="execution_job",
            details={"job_ids": [str(job_id) for job_id in deleted], "count": len(deleted)},
        )
    db.commit()
    return DeleteJobsResponse(
        deleted=deleted,
        skipped=[
            SkippedJob(job_id=job_id, reason=result)
            for job_id, result in outcome.items()
            if result != "deleted"
        ],
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
def read_job(
    job_id: UUID,
    user: Annotated[AppUser, Depends(require_committee_member)],
    repository: Annotated[Any, Depends(get_job_repository)],
) -> Any:
    if user.role == UserRole.DEPARTMENT_ADMINISTRATOR:
        job = repository.get(job_id)
    else:
        job = repository.get_for_user(job_id, user.user_id)
    if job is None:
        raise APIError(404, "NOT_FOUND", "The requested job was not found.")
    if job.job_type.value == "arm" and user.role != UserRole.DEPARTMENT_ADMINISTRATOR:
        raise APIError(
            403, "FORBIDDEN", "ARM results are restricted to administrators."
        )
    return _status(job, repository.iterations([job.job_id]).get(job.job_id))
