import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# A first-year cohort through a five-year program; anything outside this range is
# a data-entry error rather than a rare curriculum.
MIN_YEAR_LEVEL = 1
MAX_YEAR_LEVEL = 5

# Every Block seats the same number of students, so capacity is derived rather
# than entered. An expanded Block is the one approved exception and adds five,
# matching the same allowance on Rooms.
BLOCK_BASE_CAPACITY = 20
BLOCK_EXPANSION_CAPACITY = 5


class BlockSection(Base):
    """One student cohort in one term: a program, a year level, and a letter.

    A Block holds no subject. The subjects a cohort takes arrive through
    `block_section_offering`, so the same Block carries every one of its classes
    and the cohort-overlap constraint has a single row to key on rather than one
    row per subject.

    Name and capacity are derived, never stored: the name is the cohort tuple
    read back uppercase, and capacity is fixed at twenty unless the Block is
    expanded. Neither can drift from the columns, and neither is user-editable.
    """

    __tablename__ = "block_section"
    __table_args__ = (
        UniqueConstraint(
            "term_id",
            "program_code",
            "year_level",
            "section_letter",
            name="uq_block_section_cohort",
        ),
        # The `schedule_record` and association foreign keys both carry term_id
        # alongside block_id so a Block cannot be linked across terms; PostgreSQL
        # needs this unique for those references even though block_id alone is
        # already the primary key.
        UniqueConstraint("block_id", "term_id", name="uq_block_section_id_term"),
        CheckConstraint(
            f"year_level BETWEEN {MIN_YEAR_LEVEL} AND {MAX_YEAR_LEVEL}",
            name="ck_block_section_year_level_range",
        ),
        # The derived name reads the stored letter back verbatim, so storing a
        # lowercase letter would make two Blocks that display identically.
        CheckConstraint(
            "section_letter = upper(section_letter)",
            name="ck_block_section_letter_uppercase",
        ),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Not separately indexed: the cohort unique constraint is prefixed by term_id.
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("term.term_id", name="fk_block_section_term_id"),
        nullable=False,
    )
    program_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("course_program.program_code", name="fk_block_section_program_code"),
        nullable=False,
        index=True,
    )
    year_level: Mapped[int] = mapped_column(Integer, nullable=False)
    # Distinguishes parallel cohorts of the same program and year, e.g. "A".
    section_letter: Mapped[str] = mapped_column(String(5), nullable=False)
    # The one lever over capacity, and a flag rather than a number so the seat
    # count stays derived.
    is_expanded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    term: Mapped["Term"] = relationship(back_populates="block_sections")  # noqa: F821
    program: Mapped["CourseProgram"] = relationship(back_populates="block_sections")
    offering_links: Mapped[list["BlockSectionOffering"]] = relationship(  # noqa: F821
        back_populates="block",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Assignments name a class, not a Block, so this reads through the links.
    schedule_records: Mapped[list["ScheduleRecord"]] = relationship(  # noqa: F821
        secondary="block_section_offering",
        primaryjoin="BlockSection.block_id == BlockSectionOffering.block_id",
        secondaryjoin="BlockSectionOffering.class_id == ScheduleRecord.class_id",
        viewonly=True,
    )

    @property
    def name(self) -> str:
        """The identifier a user reads, e.g. "BSCS-1A"."""
        return f"{self.program_code}-{self.year_level}{self.section_letter}".upper()

    @property
    def capacity(self) -> int:
        expansion = BLOCK_EXPANSION_CAPACITY if self.is_expanded else 0
        return BLOCK_BASE_CAPACITY + expansion
