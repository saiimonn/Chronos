from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TermCreate(BaseModel):
    academic_year: str = Field(min_length=1, max_length=9)
    # No available document fixes an upper bound on term_number, so only the
    # lower bound is enforced here rather than inventing one.
    term_number: int = Field(ge=1)


class TermUpdate(BaseModel):
    academic_year: str | None = Field(default=None, min_length=1, max_length=9)
    term_number: int | None = Field(default=None, ge=1)


class TermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term_id: UUID
    academic_year: str
    term_number: int
    is_active: bool
    is_archived: bool


class TermListItem(TermResponse):
    # True when a historical snapshot exists, so the term is read-only: the
    # server refuses to select or rename it. Only the list computes this.
    is_captured: bool
