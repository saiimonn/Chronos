from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    require_committee_member,
    require_department_administrator,
)
from app.core.errors import APIError
from app.models import AppUser, GAConfiguration
from app.schemas.ga_configuration import (
    GAConfigurationResponse,
    GAConfigurationVersion,
    GAConfigurationVersionList,
)
from app.schemas.jobs import GAConfigParameters, GAConfigRequest
from app.services import ga_configuration as service
from app.services.audit import audit_rejections
from app.services.ga.config import GAConfig
from app.services.jobs.codecs import encode_ga_config

router = APIRouter(prefix="/ga-configuration", tags=["ga-configuration"])

DbSession = Annotated[Session, Depends(get_db)]


def _response(saved: GAConfiguration | None) -> GAConfigurationResponse:
    if saved is None:
        return GAConfigurationResponse(
            version=None,
            config=GAConfigParameters(**encode_ga_config(GAConfig())),
            created_at=None,
            created_by_user_id=None,
        )
    return GAConfigurationResponse(
        version=saved.version,
        config=GAConfigParameters(**saved.parameters),
        created_at=saved.created_at,
        created_by_user_id=saved.created_by_user_id,
    )


# Schedulers may read the settings their runs use; only administrators edit them.
@router.get(
    "",
    response_model=GAConfigurationResponse,
    dependencies=[Depends(require_committee_member)],
)
def read_active_configuration(db: DbSession) -> GAConfigurationResponse:
    return _response(service.get_active_configuration(db))


@router.get(
    "/versions",
    response_model=GAConfigurationVersionList,
    dependencies=[Depends(require_committee_member)],
)
def list_configuration_versions(db: DbSession) -> GAConfigurationVersionList:
    """Past versions are read-only; restoring one saves it as a new version."""
    return GAConfigurationVersionList(
        items=[
            GAConfigurationVersion(
                version=saved.version,
                is_active=saved.is_active,
                config=GAConfigParameters(**saved.parameters),
                created_at=saved.created_at,
                created_by_user_id=saved.created_by_user_id,
                created_by_username=username,
            )
            for saved, username in service.list_versions(db)
        ]
    )


@router.put("", response_model=GAConfigurationResponse)
def save_configuration(
    request: GAConfigRequest,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> GAConfigurationResponse:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="ga_configuration.save",
        target_type="ga_configuration",
        details={"requested": request.model_dump()},
    ):
        try:
            saved = service.save_configuration(
                db, GAConfig(**request.model_dump()), actor_id=user.user_id
            )
        except service.ConfigurationConflictError as error:
            raise APIError(
                409,
                "CONFIGURATION_CHANGED",
                "The GA configuration was changed by another save; reload and retry.",
            ) from error
    return _response(saved)
