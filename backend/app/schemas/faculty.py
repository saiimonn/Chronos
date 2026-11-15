from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain import Weekday, decode_preferred_days, normalize_preferred_days


def _teaching_days(days: list[Weekday] | None) -> list[Weekday] | None:
    """Any set of teaching days (or none), deduplicated, in calendar order."""
    if days is None:
        return None
    return list(normalize_preferred_days(days))


def _subject_codes(codes: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, deduplicate, and sort subject codes."""
    if codes is None:
        return None
    return sorted({code.strip() for code in codes if code.strip()})


class FacultyCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    is_full_time: bool
    is_active: bool = True
    # Soft preference only; empty means no preference.
    preferred_days: list[Weekday] = Field(default_factory=list)
    # Subjects this member prefers to teach. Soft GA input; empty means none.
    preferred_subject_codes: list[str] = Field(default_factory=list)
    # Hard: subjects this member must never be assigned.
    non_preferred_subject_codes: list[str] = Field(default_factory=list)
    # Hard: never assigned a laboratory subject.
    lecture_only: bool = False

    _check_days = field_validator("preferred_days")(_teaching_days)
    _check_subjects = field_validator(
        "preferred_subject_codes", "non_preferred_subject_codes"
    )(_subject_codes)


class FacultyUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    is_full_time: bool | None = None
    # False marks someone not teaching this term; they are never scheduled.
    is_active: bool | None = None
    # Omitted leaves the stored preference alone; [] clears it.
    preferred_days: list[Weekday] | None = None
    # Omitted leaves the stored subjects alone; [] clears them.
    preferred_subject_codes: list[str] | None = None
    # Omitted leaves the stored subjects alone; [] clears them.
    non_preferred_subject_codes: list[str] | None = None
    lecture_only: bool | None = None

    _check_days = field_validator("preferred_days")(_teaching_days)
    _check_subjects = field_validator(
        "preferred_subject_codes", "non_preferred_subject_codes"
    )(_subject_codes)


class FacultyStatusUpdate(BaseModel):
    """Activate or deactivate several members in one write."""

    faculty_ids: list[UUID] = Field(min_length=1, max_length=1000)
    is_active: bool


class FacultyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    faculty_id: UUID
    full_name: str
    is_full_time: bool
    is_active: bool
    # Preferred teaching days in calendar order; soft data, never enforced.
    preferred_days: list[Weekday]
    # Subjects this member prefers to teach, in code order. The GA favors a
    # preferring teacher for these subjects; it never blocks anyone else.
    preferred_subject_codes: list[str] = Field(default_factory=list)
    # Hard: subjects the GA never assigns this member, in code order.
    non_preferred_subject_codes: list[str] = Field(default_factory=list)
    # Hard: never assigned a laboratory subject.
    lecture_only: bool = False
    # Read-only here; written only through PUT /faculty/{id}/availability so the
    # mask keeps exactly one writer.
    availability_mask: str
    # Per-day overrides of the mask ("MON:0110;FRI:1000"); empty when every
    # day shares it. Read-only here, like the mask.
    day_availability_masks: str = ""
    # Set by confirming or saving availability; compare with the active term.
    availability_reviewed_term_id: UUID | None

    @field_validator("preferred_days", mode="before")
    @classmethod
    def _decode_days(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return list(decode_preferred_days(value))
        return value
