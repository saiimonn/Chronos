import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ScheduleUnlockedBlock(Base):
    """A Block of a master schedule opened again for editing after approval.

    Approving a Block locks it: its week is read-only until someone unlocks
    it. A row here is that unlock, so every Block approved before locks
    existed (and every Block without a row) reads as locked. Locking again
    deletes the row; finalization is refused while any row remains.
    """

    __tablename__ = "schedule_unlocked_block"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "schedule.schedule_id",
            name="fk_schedule_unlocked_block_schedule_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "block_section.block_id",
            name="fk_schedule_unlocked_block_block_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    unlocked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.user_id",
            name="fk_schedule_unlocked_block_unlocked_by",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
