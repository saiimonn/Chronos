import unicodedata
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.app_user import UserRole

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024


# The one rule for a new password: account creation, change and admin reset.
NewPassword = Annotated[
    str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str
    role: UserRole


class ManagedUserResponse(UserResponse):
    is_active: bool
    created_at: datetime


def _plain_username(value: str) -> str:
    # Refused rather than stripped: " admin " would otherwise be a
    # lookalike of "admin". isprintable() rejects NUL and the zero-width
    # format characters, which PostgreSQL cannot store or a person see.
    # NFKC refuses full-width and decomposed forms of an existing name.
    if (
        value != value.strip()
        or not value.isprintable()
        or unicodedata.normalize("NFKC", value) != value
    ):
        raise ValueError(
            "Username must be printable, NFKC-normalized, and have no "
            "leading or trailing whitespace."
        )
    return value


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: NewPassword
    role: UserRole

    @field_validator("username")
    @classmethod
    def username_is_plain(cls, value: str) -> str:
        return _plain_username(value)


class UserUpdate(BaseModel):
    """Any field may be omitted; an omitted field is left unchanged."""

    # A password in the body would otherwise be ignored with a 200.
    model_config = ConfigDict(extra="forbid")

    # A rename, by an administrator; the same rules as a new account's name.
    username: str | None = Field(default=None, min_length=1, max_length=100)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("username")
    @classmethod
    def username_is_plain(cls, value: str | None) -> str | None:
        return None if value is None else _plain_username(value)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: NewPassword


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: NewPassword
