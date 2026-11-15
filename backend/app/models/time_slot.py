import uuid
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimeSlot(Base):
    __tablename__ = "time_slot"
    __table_args__ = (
        UniqueConstraint(
            "time_slot_id", "term_id", name="uq_time_slot_identity_term"
        ),
        UniqueConstraint("term_id", "ordinal", name="uq_time_slot_term_ordinal"),
        UniqueConstraint("term_id", "label", name="uq_time_slot_term_label"),
        CheckConstraint("ordinal >= 0", name="ck_time_slot_ordinal_non_negative"),
        CheckConstraint("start_time < end_time", name="ck_time_slot_time_range"),
    )

    time_slot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("term.term_id", name="fk_time_slot_term_id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    term: Mapped["Term"] = relationship(back_populates="time_slots")  # noqa: F821
    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="canonical_time_slot",
        foreign_keys="ScheduleRecord.time_slot_id",
        passive_deletes=True,
    )
