import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Room(Base):
    """A schedulable room, keyed by its room code."""

    __tablename__ = "room"

    room_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    # Existing Rooms predate the Building catalog. New writes always set this
    # reference; legacy rows remain readable until room normalization is complete.
    building_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("building.building_id", name="fk_room_building_id"),
        nullable=True,
        index=True,
    )
    building_name: Mapped[str] = mapped_column(String(100), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    # e.g. "lecture" or "laboratory"; LAB subjects require "laboratory".
    room_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Expanded rooms have +5 capacity over the base for their type.
    expanded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # False for a room the department no longer uses. Kept so history can name
    # it; never offered to the GA or a placement edit. Server-side default only,
    # so inserts written against older migrations (which lack the column) do
    # not name it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="room",
        passive_deletes="all",
    )
    extracted_rules: Mapped[list["ExtractedRule"]] = relationship(  # noqa: F821
        back_populates="room",
        passive_deletes="all",
    )
    # Subjects restricted to rooms that include this one: the same
    # subject_allowed_room rows as Subject.allowed_rooms, seen from the room.
    allowed_subjects: Mapped[list["Subject"]] = relationship(  # noqa: F821
        secondary="subject_allowed_room",
        order_by="Subject.subject_code",
        back_populates="allowed_rooms",
    )

    @property
    def allowed_subject_codes(self) -> list[str]:
        return [subject.subject_code for subject in self.allowed_subjects]

    building: Mapped["Building | None"] = relationship(back_populates="rooms")  # noqa: F821
