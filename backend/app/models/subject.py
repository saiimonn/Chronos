from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Table, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain import SubjectType

# Rooms a subject is restricted to (e.g. networking labs only in their own
# rooms). Hard GA input: with any row, the GA draws the subject's rooms only
# from these. No rows means any fitting room. Both sides cascade.
subject_allowed_room = Table(
    "subject_allowed_room",
    Base.metadata,
    Column(
        "subject_code",
        String(20),
        ForeignKey("subject.subject_code", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    ),
    Column(
        "room_code",
        String(20),
        ForeignKey("room.room_code", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Subject(Base):
    """A catalog subject with duration derived from its LAB/LEC type."""

    __tablename__ = "subject"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('LAB', 'LEC')", name="ck_subject_type"
        ),
    )

    subject_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    subject_title: Mapped[str] = mapped_column(String(150), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(3), nullable=False)
    # False for a subject the department no longer offers. Kept so history can
    # name it; never offered to new scheduling. Server-side default only, so
    # inserts written against older migrations (which lack the column) do not
    # name it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    # Preferred day patterns, comma-separated in MW,TTh,FSa,F,Sa,Su order
    # ("TTh,F"); empty means no preference. Soft: the GA tries these first
    # and falls back to the term's other patterns when none fits. See
    # app.domain.meeting_patterns.
    preferred_day_patterns: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )

    allowed_rooms: Mapped[list["Room"]] = relationship(  # noqa: F821
        secondary=subject_allowed_room,
        lazy="selectin",
        order_by="Room.room_code",
        back_populates="allowed_subjects",
    )

    @property
    def allowed_room_codes(self) -> list[str]:
        return [room.room_code for room in self.allowed_rooms]

    @property
    def duration_minutes(self) -> int:
        return SubjectType(self.subject_type).duration_minutes

    @property
    def placements(self) -> list[dict]:
        """Where the prospecti put this subject: program, year level, term.

        Deduplicated across curriculum versions, so a subject that kept its
        slot through a revision is listed once.
        """
        seen = {
            (r.curriculum.program_code, r.year_level, r.term_number)
            for r in self.curriculum_requirements
        }
        return [
            {"program_code": p, "year_level": y, "term_number": t}
            for p, y, t in sorted(seen)
        ]

    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="subject",
        passive_deletes="all",
    )
    extracted_rules: Mapped[list["ExtractedRule"]] = relationship(  # noqa: F821
        back_populates="subject",
        passive_deletes="all",
    )
    subject_offerings: Mapped[list["SubjectOffering"]] = relationship(  # noqa: F821
        back_populates="subject",
        passive_deletes="all",
    )
    # `passive_deletes="all"` so the ORM leaves the child rows alone: without it
    # it nullifies their subject_code first, which trips NOT NULL instead of the
    # RESTRICT that is meant to refuse the delete.
    curriculum_requirements: Mapped[list["CurriculumRequirement"]] = relationship(  # noqa: F821
        back_populates="subject",
        passive_deletes="all",
    )
