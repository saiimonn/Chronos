from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL


class BlockCreate(BaseModel):
    term_id: UUID
    program_code: str = Field(min_length=1, max_length=20)
    year_level: int = Field(ge=MIN_YEAR_LEVEL, le=MAX_YEAR_LEVEL)
    section_letter: str = Field(min_length=1, max_length=5, pattern=r"^[A-Za-z0-9]+$")
    is_expanded: bool = False


class BlockUpdate(BaseModel):
    # The cohort identity is what the overlap constraint keys on, so only the
    # expansion is mutable in place. Capacity is never sent: it is derived from
    # this flag.
    is_expanded: bool | None = None


class BlockResponse(BaseModel):
    # `name` and `capacity` are read straight off the model, where both are
    # derived; neither is stored and neither can be written.
    model_config = ConfigDict(from_attributes=True)

    block_id: UUID
    term_id: UUID
    program_code: str
    year_level: int
    section_letter: str
    is_expanded: bool
    name: str
    capacity: int


class BlockClassCreate(BaseModel):
    offering_id: UUID


class BlockClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    class_id: UUID
    block_id: UUID
    offering_id: UUID
    term_id: UUID
    subject_code: str
