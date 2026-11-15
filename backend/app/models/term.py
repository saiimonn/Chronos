import uuid

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Term(Base):
    """An academic term. Historical terms feed ARM; the active term is scheduled."""

    __tablename__ = "term"
    __table_args__ = (
        UniqueConstraint("academic_year", "term_number", name="uq_term_academic_year_term_number"),
        Index("uq_term_single_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    term_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Soft delete: an archived term is hidden from setup lists but its rows and
    # everything that references it stay on file. Restoring clears the flag.
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="term",
        passive_deletes="all",
    )
    schedules: Mapped[list["Schedule"]] = relationship(  # noqa: F821
        back_populates="term",
        passive_deletes="all",
    )
    # Rule sets whose mining run drew on this term's records.
    rule_sets: Mapped[list["RuleSet"]] = relationship(  # noqa: F821
        secondary="rule_set_term", back_populates="included_terms",
        passive_deletes="all",
    )
    subject_offerings: Mapped[list["SubjectOffering"]] = relationship(  # noqa: F821
        back_populates="term",
        passive_deletes="all",
    )
    block_sections: Mapped[list["BlockSection"]] = relationship(  # noqa: F821
        back_populates="term",
        passive_deletes="all",
    )
    time_slots: Mapped[list["TimeSlot"]] = relationship(  # noqa: F821
        back_populates="term", passive_deletes="all"
    )
