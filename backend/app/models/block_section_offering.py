import uuid

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class BlockSectionOffering(Base):
    """Which Subject Offerings one Block takes — one row per class.

    A row here is what a schedule assigns: this cohort, this subject, this
    group. It carries its own `class_id` because that is the thing assignments
    reference, and because the identifier is the one the course section it
    replaced already had, so nothing that pointed at a section had to be
    rewritten when Blocks arrived.

    The subject is stored alongside the offering that already names it. That is
    what lets a cohort be stopped from taking two groups of one subject, and
    what lets `schedule_record` pin its own subject to the class it assigns.

    Both foreign keys carry `term_id`, which is what keeps a Block and its
    offering inside the same term without a separate check.
    """

    __tablename__ = "block_section_offering"
    __table_args__ = (
        ForeignKeyConstraint(
            ["block_id", "term_id"],
            ["block_section.block_id", "block_section.term_id"],
            name="fk_block_section_offering_block",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["offering_id", "term_id", "subject_code"],
            [
                "subject_offering.offering_id",
                "subject_offering.term_id",
                "subject_offering.subject_code",
            ],
            name="fk_block_section_offering_offering",
            ondelete="RESTRICT",
        ),
        # A Block takes one group of any subject, at most once.
        UniqueConstraint(
            "block_id", "offering_id", name="uq_block_section_offering_pair"
        ),
        UniqueConstraint(
            "block_id",
            "term_id",
            "subject_code",
            name="uq_block_section_offering_subject",
        ),
        # What `schedule_record` references: the class, pinned to the term and
        # subject the assignment claims for it.
        UniqueConstraint(
            "class_id",
            "term_id",
            "subject_code",
            name="uq_block_section_offering_class_term_subject",
        ),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    block: Mapped["BlockSection"] = relationship(  # noqa: F821
        back_populates="offering_links"
    )
    # Read-only: term_id is written through the Block side, so letting the
    # offering side populate it too would make two relationships fight over the
    # same column.
    offering: Mapped["SubjectOffering"] = relationship(  # noqa: F821
        back_populates="block_links", viewonly=True
    )
    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        back_populates="block_class",
        foreign_keys="ScheduleRecord.class_id",
        passive_deletes=True,
    )
