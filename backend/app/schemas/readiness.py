from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ReadinessBlockerResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class ReadinessResponse(BaseModel):
    term_id: UUID
    ready: bool
    blockers: list[ReadinessBlockerResponse]
    # Findings worth an administrator's attention that do not stop the GA, so
    # they never affect `ready`.
    warnings: list[ReadinessBlockerResponse]
