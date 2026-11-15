import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class HistoricalSnapshot(Base):
    """One finalized term captured as append-only history for mining.

    A snapshot is the unit ARM counts recurrence over, so a term may be captured
    only once: a second capture would repeat every transaction and inflate
    support for patterns that occurred once.

    The term is identified by `source_term_id` and by its denormalized year and
    term_number rather than by a foreign key. History has to keep reading correctly
    after the operational term row is renamed or removed, and a foreign key
    could only achieve that through ``ON DELETE SET NULL``, which is an UPDATE —
    exactly what the immutability triggers reject.
    """

    __tablename__ = "historical_snapshot"
    __table_args__ = (
        UniqueConstraint("source_term_id", name="uq_historical_snapshot_source_term"),
        # A recreated term row would carry a new term_id, so the academic period
        # is guarded separately.
        UniqueConstraint(
            "academic_year",
            "term_number",
            name="uq_historical_snapshot_term_period",
        ),
        CheckConstraint(
            "btrim(academic_year) <> ''",
            name="ck_historical_snapshot_academic_year_not_blank",
        ),
        CheckConstraint(
            "term_number >= 1", name="ck_historical_snapshot_term_number_positive"
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Provenance only; deliberately not a foreign key. See the class docstring.
    source_term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    # Provenance only, like source_term_id: removing operational/auth rows must
    # not mutate immutable history. Bootstrap snapshots leave these absent.
    source_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    term_number: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    records: Mapped[list["HistoricalRecord"]] = relationship(
        back_populates="snapshot",
        # ARM builds its transaction list by iterating these rows, and physical
        # row order does not survive a dump and restore, so the order is pinned
        # here to keep a mining run reproducible.
        order_by="HistoricalRecord.historical_record_id",
        # No cascade: the snapshot cannot be deleted, and SQLAlchemy must never
        # try to de-associate its records by nulling their snapshot_id.
        passive_deletes="all",
    )


class HistoricalRecord(Base):
    """One past class offering, denormalized so operational edits cannot reach it.

    Faculty, subject, and room are stored as the values they held when the term
    was captured, not as foreign keys. Renaming an instructor, retitling a
    subject, or retiring a room changes the catalog going forward; the mined
    history keeps describing what actually happened.

    `source_faculty_id` is retained because ARM groups its four streams by
    instructor identity, which two instructors sharing a name would not provide.
    It is a plain column for the same reason the snapshot's term reference is.
    """

    __tablename__ = "historical_record"
    __table_args__ = (
        # One row per captured assignment across every snapshot, not merely
        # within one: a legacy record reassigned to another term would otherwise
        # be captured a second time and counted twice by ARM. Imported rows with
        # no operational origin leave source_record_id NULL, and the index skips
        # them.
        Index(
            "uq_historical_record_source_record",
            "source_record_id",
            unique=True,
            postgresql_where=text("source_record_id IS NOT NULL"),
        ),
        # These rows can never be corrected, so a blank where a value belongs is
        # permanent: ARM would mine a rule whose consequent is the empty string.
        CheckConstraint(
            "btrim(faculty_name) <> ''",
            name="ck_historical_record_faculty_name_not_blank",
        ),
        CheckConstraint(
            "btrim(subject_code) <> ''",
            name="ck_historical_record_subject_code_not_blank",
        ),
        CheckConstraint(
            "btrim(subject_title) <> ''",
            name="ck_historical_record_subject_title_not_blank",
        ),
        CheckConstraint(
            "btrim(meeting_days) <> ''",
            name="ck_historical_record_meeting_days_not_blank",
        ),
        CheckConstraint(
            "btrim(time_slot) <> ''",
            name="ck_historical_record_time_slot_not_blank",
        ),
        CheckConstraint(
            "(meeting_days IS NULL) = (time_slot IS NULL)",
            name="ck_historical_record_days_and_time_paired",
        ),
    )

    historical_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "historical_snapshot.snapshot_id",
            name="fk_historical_record_snapshot_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    # The schedule_record this row was captured from, where there was one.
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_faculty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    faculty_name: Mapped[str] = mapped_column(String(150), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    subject_title: Mapped[str] = mapped_column(String(150), nullable=False)
    # Absent for a past offering that was never assigned a room.
    room_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Day pattern such as "MWF" and slot label such as "07:30-08:30", mined by
    # the teacher-day and teacher-time streams. Absent together for 2018-2022
    # history, which keeps only teacher and subject (FR-ING-002/003).
    meeting_days: Mapped[str | None] = mapped_column(String(10), nullable=True)
    time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)

    snapshot: Mapped["HistoricalSnapshot"] = relationship(back_populates="records")
