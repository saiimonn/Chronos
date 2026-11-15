import uuid

from sqlalchemy import Boolean, Column, ForeignKey, String, Table, Text, false, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# Subjects a faculty member prefers to teach. Soft GA input: the GA draws a
# preferring teacher first for these subjects but never blocks anyone else.
# Both sides cascade, so retiring a subject row or a faculty row never trips
# on a leftover preference.
faculty_preferred_subject = Table(
    "faculty_preferred_subject",
    Base.metadata,
    Column(
        "faculty_id",
        UUID(as_uuid=True),
        ForeignKey("faculty.faculty_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "subject_code",
        String(20),
        ForeignKey("subject.subject_code", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


# Subjects a faculty member must never be assigned. Hard constraint: the GA
# never draws them for these subjects and the validator flags a placement
# that does. Cascades on both sides, like the preferred list.
faculty_non_preferred_subject = Table(
    "faculty_non_preferred_subject",
    Base.metadata,
    Column(
        "faculty_id",
        UUID(as_uuid=True),
        ForeignKey("faculty.faculty_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "subject_code",
        String(20),
        ForeignKey("subject.subject_code", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Faculty(Base):
    """A DCISM instructor available for assignment."""

    __tablename__ = "faculty"

    faculty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    # True for full-time (max 24 units), False for part-time (max 17 units).
    is_full_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # False for someone who no longer teaches here (retired, resigned, not
    # hired this term). Kept so history can name them; never scheduled.
    # Server-side default only, so inserts written against older migrations
    # (which lack the column) do not name it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    # Hard constraint: never assigned a laboratory (LAB) subject.
    lecture_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    # Preferred teaching days, comma-separated weekday codes in calendar order
    # ("MON,WED,FRI"); empty means no preference. Soft data only: unlike the
    # mask it never blocks an assignment, and the GA does not read it.
    preferred_days: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    # Bitmask over the day/time-slot grid; a set bit means the instructor is
    # free in that slot. Read by the GA's hard-constraint check.
    availability_mask: Mapped[str] = mapped_column(String(255), nullable=False)
    # Per-day overrides of the mask above, "MON:0110;FRI:1000" in calendar
    # order; a day left out uses availability_mask. Empty means every day
    # shares the one mask, so rows saved before this column behave as before.
    day_availability_masks: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    # The term this mask was last confirmed or saved for. Preparing a new term
    # means reviewing every mask again (SRS 4.6.2), so readiness blocks while
    # this differs from the term being generated. It is a marker, not a copy:
    # availability is never versioned per term (FR-AVL-002).
    availability_reviewed_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "term.term_id",
            name="fk_faculty_availability_reviewed_term",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    # Preferred subjects, in subject-code order; empty means no preference.
    preferred_subjects: Mapped[list["Subject"]] = relationship(  # noqa: F821
        secondary=faculty_preferred_subject,
        lazy="selectin",
        order_by="Subject.subject_code",
    )

    @property
    def preferred_subject_codes(self) -> list[str]:
        return [subject.subject_code for subject in self.preferred_subjects]

    # Subjects this member must never teach, in subject-code order.
    non_preferred_subjects: Mapped[list["Subject"]] = relationship(  # noqa: F821
        secondary=faculty_non_preferred_subject,
        lazy="selectin",
        order_by="Subject.subject_code",
    )

    @property
    def non_preferred_subject_codes(self) -> list[str]:
        return [subject.subject_code for subject in self.non_preferred_subjects]

    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="faculty",
        passive_deletes="all",
    )
    extracted_rules: Mapped[list["ExtractedRule"]] = relationship(  # noqa: F821
        back_populates="faculty",
        passive_deletes="all",
    )
