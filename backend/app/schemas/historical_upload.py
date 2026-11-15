from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Base64 keeps the uploaded bytes exact, so hashes match the reviewed files.
_MAX_BASE64 = 40_000_000


class HistoricalUploadRequest(BaseModel):
    """The Offered Courses workbook (.xlsx), base64-encoded."""

    model_config = ConfigDict(extra="forbid")

    workbook: str = Field(min_length=1, max_length=_MAX_BASE64)


class HistoricalUploadConfirmRequest(BaseModel):
    """The content hash the administrator reviewed."""

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HistoricalUploadRejectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file: str
    line: int | None
    reason: str


class HistoricalUploadBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    status: str
    content_sha256: str
    accepted_count: int
    rejected_count: int
    skipped_count: int
    populated_period_count: int
    empty_period_count: int
    unknown_subject_count: int | None
    unknown_room_count: int | None
    uploaded_at: datetime
    committed_at: datetime | None


class HistoricalUploadBatchDetailResponse(HistoricalUploadBatchResponse):
    # Rows that prevent confirmation, and rows skipped as not history to import.
    rejections: list[HistoricalUploadRejectionResponse] = Field(
        validation_alias="blocking_rejections"
    )
    skipped_rows: list[HistoricalUploadRejectionResponse]


class HistoricalUploadCommitResponse(BaseModel):
    batch: HistoricalUploadBatchResponse
    snapshot_ids: list[UUID]
    mining_or_activation_performed: bool = False
