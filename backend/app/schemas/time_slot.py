from datetime import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeSlotCreate(BaseModel):
    term_id: UUID
    label: str = Field(min_length=1, max_length=50)
    start_time: time
    end_time: time
    ordinal: int = Field(ge=0)

    @model_validator(mode="after")
    def check_time_range(self) -> "TimeSlotCreate":
        # Mirrors ck_time_slot_time_range so a bad range is a 422 rather than a
        # database error surfacing as a 500.
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self


class TimeSlotUpdate(BaseModel):
    # Omitted/null fields are no-ops; the service refuses structural changes.
    label: str | None = Field(default=None, min_length=1, max_length=50)
    start_time: time | None = None
    end_time: time | None = None
    ordinal: int | None = Field(default=None, ge=0)


class TimeSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_slot_id: UUID
    term_id: UUID
    label: str
    start_time: time
    end_time: time
    ordinal: int
