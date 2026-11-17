"""Hard Constraints page API contracts."""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain import ConstraintLimits

_DEFAULTS = ConstraintLimits()


class ConstraintLimitsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_time_max_units: int = Field(default=_DEFAULTS.full_time_max_units, ge=1, le=60)
    part_time_max_units: int = Field(default=_DEFAULTS.part_time_max_units, ge=1, le=60)
    max_consecutive_lectures: int = Field(
        default=_DEFAULTS.max_consecutive_lectures, ge=1, le=10
    )
    max_consecutive_labs: int = Field(default=_DEFAULTS.max_consecutive_labs, ge=1, le=10)
    block_day_target_hours: int = Field(
        default=_DEFAULTS.block_day_target_hours, ge=1, le=24
    )
    block_day_limit_hours: int = Field(default=_DEFAULTS.block_day_limit_hours, ge=1, le=24)
    block_run_limit_hours: int = Field(default=_DEFAULTS.block_run_limit_hours, ge=1, le=24)

    @model_validator(mode="after")
    def validate_relations(self) -> "ConstraintLimitsModel":
        self.to_limits()  # ConstraintLimits owns the cross-field rules.
        return self

    def to_limits(self) -> ConstraintLimits:
        return ConstraintLimits(**self.model_dump())

    @classmethod
    def from_limits(cls, limits: ConstraintLimits) -> "ConstraintLimitsModel":
        return cls(**asdict(limits))


class HardConstraintsResponse(BaseModel):
    """The active limits; ``version`` is null while the defaults apply."""

    version: int | None
    limits: ConstraintLimitsModel
    defaults: ConstraintLimitsModel
    created_at: datetime | None
    created_by_user_id: UUID | None


class HardConstraintsVersion(BaseModel):
    version: int
    is_active: bool
    limits: ConstraintLimitsModel
    created_at: datetime
    created_by_username: str | None


class HardConstraintsVersionList(BaseModel):
    items: list[HardConstraintsVersion]
