from datetime import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain import Weekday, normalize_preferred_days


class SlotGridEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_slot_id: UUID
    label: str
    start_time: time
    end_time: time
    ordinal: int


class AvailabilityResponse(BaseModel):
    faculty_id: UUID
    term_id: UUID
    # The grid travels with the selection so a client can render the whole week
    # without a second request, and never has to infer ordinal ordering.
    slot_grid: list[SlotGridEntry]
    time_slot_ids: list[UUID]
    # Days with their own hours; a day left out uses time_slot_ids.
    day_time_slot_ids: dict[Weekday, list[UUID]] = {}
    # Whether this selection was confirmed or saved for the active term.
    reviewed: bool
    # Soft day preference shown beside the mask; never a hard constraint.
    preferred_days: list[Weekday]


class AvailabilityUpdate(BaseModel):
    term_id: UUID
    time_slot_ids: list[UUID]
    # Saved with the mask in one write; omitted leaves the stored preference.
    preferred_days: list[Weekday] | None = None
    # Per-day overrides replacing every stored one; {} clears them so every
    # day uses time_slot_ids, and omitted leaves the stored overrides.
    day_time_slot_ids: dict[Weekday, list[UUID]] | None = None

    @field_validator("day_time_slot_ids")
    @classmethod
    def _override_days(
        cls, days: dict[Weekday, list[UUID]] | None
    ) -> dict[Weekday, list[UUID]] | None:
        if days is not None:
            normalize_preferred_days(list(days))  # teaching days only
        return days

    @field_validator("preferred_days")
    @classmethod
    def _teaching_days(cls, days: list[Weekday] | None) -> list[Weekday] | None:
        return None if days is None else list(normalize_preferred_days(days))


class AvailabilityConfirm(BaseModel):
    # What the operator's dialog showed; a confirmation is refused when either
    # no longer matches the active term and stored selection.
    term_id: UUID
    time_slot_ids: list[UUID]
    # The per-day overrides the dialog showed; omitted skips that check, for
    # clients written before per-day hours.
    day_time_slot_ids: dict[Weekday, list[UUID]] | None = None
