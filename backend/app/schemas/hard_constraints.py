"""Hard Constraints page API contracts."""

from collections.abc import Mapping
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
    block_day_target_hours: int = Field(
        default=_DEFAULTS.block_day_target_hours, ge=1, le=24
    )
    block_day_limit_hours: int = Field(default=_DEFAULTS.block_day_limit_hours, ge=1, le=24)
    back_to_back_limit_hours: int = Field(
        default=_DEFAULTS.back_to_back_limit_hours, ge=1, le=24
    )

    @model_validator(mode="after")
    def validate_relations(self) -> "ConstraintLimitsModel":
        self.to_limits()  # ConstraintLimits owns the cross-field rules.
        return self

    def to_limits(self) -> ConstraintLimits:
        return ConstraintLimits(**self.model_dump())

    @classmethod
    def from_limits(cls, limits: ConstraintLimits) -> "ConstraintLimitsModel":
        return cls(**asdict(limits))

    @classmethod
    def from_saved(cls, value: Mapping[str, object]) -> "ConstraintLimitsModel":
        """A stored version, including ones saved with earlier field names."""
        return cls.from_limits(ConstraintLimits.from_saved(value))


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
