from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssignmentResponse(BaseModel):
    record_id: UUID
    class_id: UUID
    offering_id: UUID
    block_id: UUID
    faculty_id: UUID
    subject_code: str
    room_code: str | None
    meeting_days: str | None
    time_slot_id: UUID
    time_slot: str | None


class AssignmentUpdate(BaseModel):
    faculty_id: UUID | None = None
    room_code: str | None = Field(default=None, min_length=1, max_length=20)
    meeting_days: str | None = Field(default=None, min_length=1, max_length=10)
    time_slot_id: UUID | None = None


class ConflictResponse(BaseModel):
    code: str
    resource_key: list[str]
    class_ids: list[UUID]
    time_slot_ids: list[UUID]
    message: str


class ValidationResponse(BaseModel):
    required_class_count: int
    assigned_class_count: int
    completeness_percentage: float
    unassigned_class_ids: list[UUID]
    hard_conflict_count: int
    conflicts: list[ConflictResponse]
    export_allowed: bool


class ScheduleResponse(BaseModel):
    schedule_id: UUID
    term_id: UUID
    schedule_type: Literal["routine", "master"]
    created_at: datetime
    finalized_at: datetime | None
    # Master schedules: approved Blocks opened again for editing. Every other
    # approved Block is locked.
    unlocked_block_ids: list[UUID] = []
    assignments: list[AssignmentResponse]


class TimetableCalendarResponse(BaseModel):
    teaching_days: list[str]
    opening_time: time
    closing_time: time
    interval_minutes: int


class TimetableAssignmentResponse(BaseModel):
    record_id: UUID
    offering_id: UUID
    subject_code: str
    subject_title: str
    group_label: str
    block_id: UUID
    block_name: str
    program_code: str
    year_level: int
    faculty_id: UUID
    faculty_name: str
    room_code: str | None
    room_type: str | None
    room_capacity: int | None
    meeting_days: str | None
    time_slot_id: UUID
    time_slot_label: str
    start_time: time
    end_time: time


class TimetableProjectionResponse(BaseModel):
    schedule_id: UUID
    term_id: UUID
    schedule_type: Literal["routine", "master"]
    created_at: datetime
    finalized_at: datetime | None
    finalization_state: Literal["candidate", "finalized"]
    calendar: TimetableCalendarResponse
    assignments: list[TimetableAssignmentResponse]


class MasterScheduleCreate(BaseModel):
    term_id: UUID


class ScheduleLifecycleResponse(BaseModel):
    schedule: ScheduleResponse
    validation: ValidationResponse


class ReplacementAssignmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID
    faculty_id: UUID
    room_code: str = Field(min_length=1, max_length=20)
    time_slot_id: UUID
    meeting_days: str = Field(min_length=1, max_length=10)


class BlockReplacementPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID | None = Field(
        default=None, description="Completed per-Block GA job ID"
    )
    assignments: list[ReplacementAssignmentPayload] | None = Field(
        default=None, description="Explicit proposed assignments for the Block"
    )
    strict_class_ids: list[UUID] | None = Field(
        default=None,
        max_length=200,
        description=(
            "Classes of this Block just edited in review. If the independent "
            "validator reports any hard conflict touching one of them, the "
            "preview is refused (409 PROPOSAL_EDIT_CONFLICT) with the reasons."
        ),
    )

    @model_validator(mode="after")
    def validate_source(self) -> "BlockReplacementPreviewRequest":
        if self.job_id is None and self.assignments is None:
            raise ValueError("Either job_id or assignments must be provided")
        if self.job_id is not None and self.assignments is not None:
            raise ValueError("Provide either job_id or assignments, not both")
        return self


class ReplacementAssignmentDetail(BaseModel):
    class_id: UUID
    offering_id: UUID
    block_id: UUID
    faculty_id: UUID
    subject_code: str
    room_code: str
    meeting_days: str
    time_slot_id: UUID
    time_slot: str


class AssignmentDiff(BaseModel):
    class_id: UUID
    subject_code: str
    status: Literal["added", "modified", "removed", "unchanged"]
    changed_fields: list[str]
    current_assignment: ReplacementAssignmentDetail | None = None
    proposed_assignment: ReplacementAssignmentDetail | None = None


class BlockReplacementPreviewResponse(BaseModel):
    preview_token: str
    schedule_id: UUID
    block_id: UUID
    proposed_assignments: list[ReplacementAssignmentDetail]
    changed_values: list[AssignmentDiff]
    validation: ValidationResponse


class BlockReplacementConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str = Field(description="Token returned by preview endpoint")
    assignments: list[ReplacementAssignmentPayload] | None = Field(
        default=None,
        description="Optional proposed assignments matching the preview; if omitted, taken from preview token",
    )

