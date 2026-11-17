from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.core.errors import APIError
from app.domain import ConstraintLimits
from app.models import AppUser, HardConstraintConfiguration
from app.schemas.hard_constraints import (
    ConstraintLimitsModel,
    HardConstraintsResponse,
    HardConstraintsVersion,
    HardConstraintsVersionList,
)
from app.services import hard_constraints as service
from app.services.audit import audit_rejections

router = APIRouter(prefix="/hard-constraints", tags=["hard-constraints"])

DbSession = Annotated[Session, Depends(get_db)]


def _response(saved: HardConstraintConfiguration | None) -> HardConstraintsResponse:
    defaults = ConstraintLimitsModel.from_limits(ConstraintLimits())
    return HardConstraintsResponse(
        version=saved.version if saved else None,
        limits=ConstraintLimitsModel.from_saved(saved.limits) if saved else defaults,
        defaults=defaults,
        created_at=saved.created_at if saved else None,
        created_by_user_id=saved.created_by_user_id if saved else None,
    )


# Schedulers may read the limits their schedules are checked against; only
# administrators change them.
@router.get(
    "",
    response_model=HardConstraintsResponse,
    dependencies=[Depends(require_committee_member)],
)
def read_active_limits(db: DbSession) -> HardConstraintsResponse:
    return _response(service.get_active_configuration(db))


@router.get(
    "/versions",
    response_model=HardConstraintsVersionList,
    dependencies=[Depends(require_committee_member)],
)
def list_limit_versions(db: DbSession) -> HardConstraintsVersionList:
    return HardConstraintsVersionList(
        items=[
            HardConstraintsVersion(
                version=saved.version,
                is_active=saved.is_active,
                limits=ConstraintLimitsModel.from_saved(saved.limits),
                created_at=saved.created_at,
                created_by_username=username,
            )
            for saved, username in service.list_versions(db)
        ]
    )


@router.put("", response_model=HardConstraintsResponse)
def save_limits(
    request: ConstraintLimitsModel,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> HardConstraintsResponse:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="hard_constraints.save",
        target_type="hard_constraint_configuration",
        details={"requested": request.model_dump()},
    ):
        try:
            saved = service.save_configuration(
                db, request.to_limits(), actor_id=user.user_id
            )
        except service.ConfigurationConflictError as error:
            raise APIError(
                409,
                "CONFIGURATION_CHANGED",
                "The hard-constraint limits were changed by another save; "
                "reload and retry.",
            ) from error
    return _response(saved)
