import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ScheduleRecord(Base):
    """One class assignment.

    Serves double duty: `is_historical` rows are past offerings mined by ARM,
    while the rest are slots the GA produces for the active term. A row that is
    not yet placed has no room, meeting days, or time slot.
    """

    __tablename__ = "schedule_record"
    __table_args__ = (
        ForeignKeyConstraint(
            ["schedule_id", "term_id"],
            ["schedule.schedule_id", "schedule.term_id"],
            name="fk_schedule_record_schedule_term",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["class_id", "term_id", "subject_code"],
            [
                "block_section_offering.class_id",
                "block_section_offering.term_id",
                "block_section_offering.subject_code",
            ],
            name="fk_schedule_record_class_term_subject",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["time_slot_id", "term_id"],
            ["time_slot.time_slot_id", "time_slot.term_id"],
            name="fk_schedule_record_time_slot_term",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(schedule_id IS NULL AND class_id IS NULL AND time_slot_id IS NULL) "
            "OR (schedule_id IS NOT NULL AND class_id IS NOT NULL "
            "AND time_slot_id IS NOT NULL)",
            name="ck_schedule_record_assignment_provenance",
        ),
        UniqueConstraint(
            "schedule_id",
            "class_id",
            name="uq_schedule_record_schedule_class",
        ),
    )

    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    faculty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faculty.faculty_id"), nullable=False, index=True
    )
    subject_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("subject.subject_code"), nullable=False, index=True
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("term.term_id"), nullable=False, index=True
    )
    # Not separately indexed: uq_schedule_record_schedule_class leads with
    # schedule_id.
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    time_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    room_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("room.room_code"), nullable=True, index=True
    )
    # Day pattern such as "MWF" or "TTh".
    meeting_days: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Slot label such as "07:30-08:30".
    time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_historical: Mapped[bool] = mapped_column(Boolean, nullable=False)

    faculty: Mapped["Faculty"] = relationship(  # noqa: F821
        back_populates="schedule_records"
    )
    subject: Mapped["Subject"] = relationship(  # noqa: F821
        back_populates="schedule_records"
    )
    term: Mapped["Term"] = relationship(back_populates="schedule_records")  # noqa: F821
    schedule: Mapped["Schedule | None"] = relationship(  # noqa: F821
        back_populates="records", foreign_keys=[schedule_id]
    )
    block_class: Mapped["BlockSectionOffering | None"] = relationship(  # noqa: F821
        back_populates="schedule_records", foreign_keys=[class_id]
    )
    canonical_time_slot: Mapped["TimeSlot | None"] = relationship(  # noqa: F821
        back_populates="schedule_records", foreign_keys=[time_slot_id]
    )
    room: Mapped["Room | None"] = relationship(  # noqa: F821
        back_populates="schedule_records"
    )
