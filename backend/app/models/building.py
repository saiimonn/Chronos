import uuid

from sqlalchemy import Boolean, CheckConstraint, Index, String, func, text, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Building(Base):
    """An administrator-managed building catalog entry."""

    __tablename__ = "building"
    __table_args__ = (
        CheckConstraint(
            "btrim(building_name) <> ''", name="ck_building_name_nonblank"
        ),
        CheckConstraint(
            "btrim(building_code) <> '' AND building_code = upper(building_code)",
            name="ck_building_code_uppercase_nonblank",
        ),
        # Names are prose, so they cannot be canonicalized the way codes are.
        # Folding case in the index is what makes "Lawton" and "LAWTON" one
        # building, which Room lookups by name rely on.
        Index("uq_building_name_lower", func.lower(text("building_name")), unique=True),
    )

    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    building_name: Mapped[str] = mapped_column(String(100), nullable=False)
    building_code: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True
    )
    # False for a building the department no longer uses. Kept so its rooms'
    # history can name it; new rooms cannot be put in it. Server-side default
    # only, so inserts written against older migrations do not name it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    rooms: Mapped[list["Room"]] = relationship(  # noqa: F821
        back_populates="building", passive_deletes="all"
    )
