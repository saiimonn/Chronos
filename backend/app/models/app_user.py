import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class UserRole(StrEnum):
    DEPARTMENT_ADMINISTRATOR = "department_administrator"
    SCHEDULING_COMMITTEE = "scheduling_committee"


USER_ROLE_SQL_LIST = ", ".join(f"'{role.value}'" for role in UserRole)


class AppUser(Base):
    """A locally managed Chronos user with one of the two approved roles."""

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("username", name="uq_app_user_username"),
        CheckConstraint("btrim(username) <> ''", name="ck_app_user_username_not_blank"),
        CheckConstraint(
            "password_hash LIKE '$argon2id$%'",
            name="ck_app_user_password_hash_argon2id",
        ),
        CheckConstraint(
            f"role IN ({USER_ROLE_SQL_LIST})",
            name="ck_app_user_role",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    sessions: Mapped[list["AuthSession"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
