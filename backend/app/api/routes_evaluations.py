from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.core.config import settings
from app.core.errors import APIError
from app.models import AppUser
from app.repositories.jobs import JobRepository
from app.repositories.protocols import SchedulingProblemNotFoundError
from app.schemas.evaluation import SubmitEvaluationRequest
from app.schemas.jobs import JobAcceptedResponse
from app.services import evaluation as service
from app.services.arm.providers import RuleSetNotFoundError
from app.services.audit import audit_rejections, record_event
from app.services.ga import GAConfig
from app.services.schedule_export import CSV_MEDIA_TYPE, XLSX_MEDIA_TYPE


router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(require_committee_member)],
)
DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_evaluation(
    request: SubmitEvaluationRequest,
    response: Response,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> JobAcceptedResponse:
    """Queue ten matched guided/unguided pairs on the local worker."""
    try:
        job = service.submit_matched_evaluation(
            db,
            JobRepository(db),
            term_id=request.term_id,
            rule_set_id=request.rule_set_id,
            seeds=request.seeds,
            config=GAConfig(**request.config.model_dump()),
            requested_by=user.user_id,
            timeout_seconds=settings.job_evaluation_timeout_seconds,
        )
    except SchedulingProblemNotFoundError as error:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.") from error
    except RuleSetNotFoundError as error:
        raise APIError(404, "NOT_FOUND", "The requested rule set was not found.") from error
    except service.EvaluationHoldoutLeakError as error:
        raise APIError(
            409,
            "RULE_SET_INCLUDES_HOLDOUT",
            "The selected rule set was mined from holdout or newer terms; use a "
            "rule set mined only from years before the holdout year.",
            details={"withheld_periods": list(error.periods)},
        ) from error
    except service.EvaluationProvenanceUnknownError as error:
        raise APIError(
            409,
            "RULE_SET_PROVENANCE_UNKNOWN",
            "The selected rule set's source terms are missing or unreadable, so "
            "it cannot be shown to exclude the holdout year.",
        ) from error
    except service.EvaluationGuidanceError as error:
        raise APIError(
            409,
            "RULE_SET_INVALID",
            "The selected rule set is invalid; an administrator must review it first.",
        ) from error
    response.headers["Location"] = f"/api/v1/jobs/{job.job_id}"
    return JobAcceptedResponse(
        job_id=job.job_id, kind=job.job_type.value, status=job.status.value
    )


@router.get("/{evaluation_batch_id}/export")
def export_evaluation(
    evaluation_batch_id: UUID,
    db: DbSession,
    user: Annotated[AppUser, Depends(get_current_user)],
    format: Annotated[Literal["csv", "xlsx", "json"], Query()] = "csv",
) -> Response:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="evaluation.export",
        target_type="evaluation_batch",
        target_id=evaluation_batch_id,
        details={"format": format},
    ):
        batch = service.get_evaluation(db, evaluation_batch_id)
        content = service.export_evaluation(batch, format)
    record_event(
        db,
        actor_id=user.user_id,
        operation="evaluation.export",
        target_type="evaluation_batch",
        target_id=evaluation_batch_id,
        details={"format": format, "byte_count": len(content)},
    )
    db.commit()
    return Response(
        content=content,
        media_type={
            "csv": CSV_MEDIA_TYPE, "xlsx": XLSX_MEDIA_TYPE, "json": "application/json"
        }[format],
        headers={
            "Content-Disposition": (
                f'attachment; filename="chronos-evaluation-{evaluation_batch_id}.{format}"'
            )
        },
    )
