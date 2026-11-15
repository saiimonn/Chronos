"""CHR-110 / CHR-ING-002 administrator completed-term capture contract."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator


class HistoricalCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: UUID
    confirm_completed_and_approved: StrictBool

    @field_validator("confirm_completed_and_approved")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("explicit completed-term approval is required")
        return value


class HistoricalCaptureResponse(BaseModel):
    snapshot_id: UUID
    source_term_id: UUID
    source_schedule_id: UUID | None
    approved_by_user_id: UUID | None
    academic_year: str
    term_number: int
    captured_at: datetime
    record_count: int


class HistoricalRecordResponse(BaseModel):
    """One past class as it was recorded. Room, days and time are absent for
    2018-2022 history, which keeps only teacher and subject."""

    model_config = ConfigDict(from_attributes=True)

    historical_record_id: UUID
    # Tells apart two teachers who share a name.
    source_faculty_id: UUID
    faculty_name: str
    subject_code: str
    subject_title: str
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
