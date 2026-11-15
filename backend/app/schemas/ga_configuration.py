"""CHR-119 / CHR-CFG-001 GA configuration API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.jobs import GAConfigParameters


class GAConfigurationResponse(BaseModel):
    """The active configuration; ``version`` is null only before any is saved."""

    version: int | None
    config: GAConfigParameters
    created_at: datetime | None
    created_by_user_id: UUID | None


class GAConfigurationVersion(BaseModel):
    """One saved version, as listed in the configuration history."""

    version: int
    is_active: bool
    config: GAConfigParameters
    created_at: datetime
    created_by_user_id: UUID | None
    created_by_username: str | None


class GAConfigurationVersionList(BaseModel):
    """Every saved version, newest first."""

    items: list[GAConfigurationVersion]
