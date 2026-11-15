import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain import RoomType, SubjectType
from app.domain.meeting_patterns import (
    decode_preferred_patterns,
    normalize_preferred_patterns,
)
from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL


def normalize_building_name(value: str) -> str:
    """Trim a building name so writes and lookups agree on one spelling.

    Room writes name their building, so the same trimming has to happen on both
    sides or a padded name silently fails to resolve.
    """
    value = value.strip()
    if not value:
        raise ValueError("building_name must not be blank")
    return value


def normalize_building_code(value: str) -> str:
    value = value.strip().upper()
    if not value:
        raise ValueError("building_code must not be blank")
    return value


class BuildingCreate(BaseModel):
    building_name: str = Field(min_length=1, max_length=100)
    building_code: str = Field(min_length=1, max_length=20)

    @field_validator("building_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_building_name(value)

    @field_validator("building_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return normalize_building_code(value)


class BuildingUpdate(BaseModel):
    building_name: str | None = Field(default=None, min_length=1, max_length=100)
    building_code: str | None = Field(default=None, min_length=1, max_length=20)
    # False retires the record: kept for history, never offered to scheduling.
    is_active: bool | None = None

    @field_validator("building_name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        return normalize_building_name(value) if value is not None else None

    @field_validator("building_code")
    @classmethod
    def normalize_optional_code(cls, value: str | None) -> str | None:
        return normalize_building_code(value) if value is not None else None


class BuildingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    building_id: UUID
    building_name: str
    building_code: str
    is_active: bool


def _preferred_patterns(patterns: list[str] | None) -> list[str] | None:
    return None if patterns is None else list(normalize_preferred_patterns(patterns))


def _room_codes(codes: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, deduplicate, and sort room (or subject) codes."""
    if codes is None:
        return None
    return sorted({code.strip() for code in codes if code.strip()})


class SubjectCreate(BaseModel):
    subject_code: str = Field(min_length=1, max_length=20)
    subject_title: str = Field(min_length=1, max_length=150)
    units: int = Field(gt=0)
    subject_type: SubjectType
    # Soft day preference: any of MW, TTh, FSa, F, Sa, Su; empty means none.
    preferred_day_patterns: list[str] = Field(default_factory=list)
    # Hard room restriction; empty means any fitting room.
    allowed_room_codes: list[str] = Field(default_factory=list)

    _check_patterns = field_validator("preferred_day_patterns")(_preferred_patterns)
    _check_rooms = field_validator("allowed_room_codes")(_room_codes)


class SubjectUpdate(BaseModel):
    # subject_code is omitted on purpose: it is a foreign-key target, so it is
    # immutable once created.
    subject_title: str | None = Field(default=None, min_length=1, max_length=150)
    units: int | None = Field(default=None, gt=0)
    subject_type: SubjectType | None = None
    # False retires the record: kept for history, never offered to scheduling.
    is_active: bool | None = None
    # [] clears the preference.
    preferred_day_patterns: list[str] | None = None
    # [] lifts the restriction.
    allowed_room_codes: list[str] | None = None

    _check_patterns = field_validator("preferred_day_patterns")(_preferred_patterns)
    _check_rooms = field_validator("allowed_room_codes")(_room_codes)


class SubjectPlacement(BaseModel):
    program_code: str
    year_level: int
    term_number: int


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subject_code: str
    subject_title: str
    units: int
    subject_type: SubjectType
    duration_minutes: int
    is_active: bool
    preferred_day_patterns: list[str]
    allowed_room_codes: list[str] = []
    # Curriculum slots naming this subject; empty for electives and GE.
    placements: list[SubjectPlacement] = []

    @field_validator("preferred_day_patterns", mode="before")
    @classmethod
    def _decode_patterns(cls, value):
        return list(decode_preferred_patterns(value)) if isinstance(value, str) else value


ROOM_CAPACITY_MAX = 500


class RoomCreate(BaseModel):
    room_code: str = Field(min_length=1, max_length=20)
    building_name: str = Field(min_length=1, max_length=100)
    room_type: RoomType
    expanded: bool = False
    # Omitted or null: derived from room_type and expanded. A number records
    # the room's actual seat count when it differs from the derived default.
    capacity: int | None = Field(default=None, ge=1, le=ROOM_CAPACITY_MAX)
    # Subjects to restrict to this room (adds it to each one's allowed rooms).
    allowed_subject_codes: list[str] = Field(default_factory=list)

    _check_subjects = field_validator("allowed_subject_codes")(_room_codes)

    @field_validator("building_name")
    @classmethod
    def normalize_building(cls, value: str) -> str:
        return normalize_building_name(value)


class RoomUpdate(BaseModel):
    building_name: str | None = Field(default=None, min_length=1, max_length=100)
    room_type: RoomType | None = None
    expanded: bool | None = None
    # A number overrides the seat count; null resets it to the derived value.
    # Omitted, it is re-derived only when room_type or expanded changes.
    capacity: int | None = Field(default=None, ge=1, le=ROOM_CAPACITY_MAX)
    # False retires the record: kept for history, never offered to scheduling.
    is_active: bool | None = None
    # Replaces the subjects whose allowed rooms include this room; [] removes
    # the room from every subject's allowed rooms.
    allowed_subject_codes: list[str] | None = None

    _check_subjects = field_validator("allowed_subject_codes")(_room_codes)

    @field_validator("building_name")
    @classmethod
    def normalize_optional_building(cls, value: str | None) -> str | None:
        return normalize_building_name(value) if value is not None else None


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    room_code: str
    building_name: str
    capacity: int
    room_type: RoomType
    expanded: bool
    is_active: bool
    allowed_subject_codes: list[str] = []


class ProgramCreate(BaseModel):
    program_code: str = Field(min_length=1, max_length=20)
    program_name: str = Field(min_length=1, max_length=150)


class ProgramUpdate(BaseModel):
    program_name: str | None = Field(default=None, min_length=1, max_length=150)
    # False retires the record: kept for history, never offered to scheduling.
    is_active: bool | None = None


class ProgramResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    program_code: str
    program_name: str
    is_active: bool


class CohortGenerateRequest(BaseModel):
    """One cohort of one program: the Blocks it runs this term, by letter.

    The term comes from the path, so a request cannot name a different term
    from the one it is sent to.
    """

    program_code: str = Field(min_length=1, max_length=20)
    year_level: int = Field(ge=MIN_YEAR_LEVEL, le=MAX_YEAR_LEVEL)
    section_letters: list[str] = Field(min_length=1, max_length=26)

    @field_validator("section_letters")
    @classmethod
    def normalize_letters(cls, value: list[str]) -> list[str]:
        # Stored uppercase, like a manually created Block, and deduplicated so
        # "A, a" asks for one Block rather than failing on the second.
        letters: list[str] = []
        for letter in value:
            letter = letter.strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{1,5}", letter):
                raise ValueError(
                    "each section letter must be 1 to 5 letters or digits"
                )
            if letter not in letters:
                letters.append(letter)
        return letters


class CohortClassReport(BaseModel):
    class_id: UUID
    offering_id: UUID
    subject_code: str
    group_no: int
    group_label: str
    created: bool


class CohortBlockReport(BaseModel):
    block_id: UUID
    name: str
    section_letter: str
    created: bool
    classes: list[CohortClassReport]
    # Set when the curriculum requires nothing of this cohort this term, which
    # is a valid answer (year 3 has no summer term), not a failure.
    note: str | None = None


class CohortGenerationResponse(BaseModel):
    term_id: UUID
    program_code: str
    year_level: int
    term_number: int
    curriculum_id: UUID
    curriculum_label: str
    required_subject_codes: list[str]
    blocks: list[CohortBlockReport]
    blocks_created: int
    offerings_created: int
    classes_created: int
