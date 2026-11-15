from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OfferingCreate(BaseModel):
    # Group No. is deliberately absent: it is allocated by the server, so a
    # client that sends one is ignored rather than obeyed.
    term_id: UUID
    subject_code: str = Field(min_length=1, max_length=20)


class OfferingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offering_id: UUID
    term_id: UUID
    subject_code: str
    group_no: int
    # The label callers display, so no consumer has to rebuild the spelling.
    group_label: str
