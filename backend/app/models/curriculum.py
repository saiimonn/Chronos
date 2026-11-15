import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.block_section import MAX_YEAR_LEVEL, MIN_YEAR_LEVEL

# The department has run three terms per academic year since the 2023 move off
# the two-semester calendar. A number outside this range is a data-entry error
# rather than a rare curriculum.
MIN_TERM_NUMBER = 1
MAX_TERM_NUMBER = 3


class Curriculum(Base):
    """One program's prospectus, as it stood for the cohorts that entered under it.

    `effective_year` is the entry academic year the prospectus governs, not the
    year it is used in. Cohorts enrolled at the same time follow different
    prospecti — a fourth-year Block in 2026-2027 entered in 2023-2024 — so a
    single curriculum per program would put older cohorts on the wrong subject
    list. The department also moved from two semesters to three terms in 2023,
    inside the range of cohorts still enrolled, so the versions are real rather
    than defensive.

    A curriculum holds no subjects itself. What a cohort must take arrives
    through `curriculum_requirement`, one row per subject per term, so the same
    curriculum carries every year level it covers.
    """

    __tablename__ = "curriculum"
    __table_args__ = (
        UniqueConstraint(
            "program_code",
            "effective_year",
            name="uq_curriculum_program_effective_year",
        ),
        # Matches the stored shape of `term.academic_year`. Resolution compares
        # these as plain strings, which is only correct while the format is
        # fixed-width, so the format is enforced here rather than assumed.
        CheckConstraint(
            "effective_year ~ '^[0-9]{4}-[0-9]{4}$'",
            name="ck_curriculum_effective_year_format",
        ),
    )

    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Not separately indexed: the identity unique constraint is prefixed by
    # program_code, and every read resolves one program at a time.
    program_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("course_program.program_code", name="fk_curriculum_program_code"),
        nullable=False,
    )
    effective_year: Mapped[str] = mapped_column(String(9), nullable=False)

    program: Mapped["CourseProgram"] = relationship(  # noqa: F821
        back_populates="curricula"
    )
    requirements: Mapped[list["CurriculumRequirement"]] = relationship(  # noqa: F821
        back_populates="curriculum",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def label(self) -> str:
        """The identifier a user reads, e.g. "BSCS 2023-2024"."""
        return f"{self.program_code} {self.effective_year}"


class CurriculumRequirement(Base):
    """One subject a cohort must take, at one year level, in one term.

    This is what makes a cohort's subject list checkable. Before it, the only
    record of what a Block takes was the `block_section_offering` rows an
    administrator had already created, so an omission was indistinguishable
    from a deliberate choice.

    The subject is referenced, never copied. A requirement naming a subject
    that no longer exists would quietly drop out of a cohort's list, so the
    foreign key restricts the delete instead.
    """

    __tablename__ = "curriculum_requirement"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_id",
            "year_level",
            "term_number",
            "subject_code",
            name="uq_curriculum_requirement_placement",
        ),
        CheckConstraint(
            f"year_level BETWEEN {MIN_YEAR_LEVEL} AND {MAX_YEAR_LEVEL}",
            name="ck_curriculum_requirement_year_level_range",
        ),
        CheckConstraint(
            f"term_number BETWEEN {MIN_TERM_NUMBER} AND {MAX_TERM_NUMBER}",
            name="ck_curriculum_requirement_term_number_range",
        ),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Not separately indexed: the placement unique constraint is prefixed by
    # curriculum_id, which also serves the cascade.
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "curriculum.curriculum_id",
            name="fk_curriculum_requirement_curriculum_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    year_level: Mapped[int] = mapped_column(Integer, nullable=False)
    term_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Indexed on its own: no unique constraint here starts with subject_code, so
    # without it the RESTRICT check on deleting a Subject scans the whole table.
    subject_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey(
            "subject.subject_code",
            name="fk_curriculum_requirement_subject_code",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    curriculum: Mapped["Curriculum"] = relationship(back_populates="requirements")
    subject: Mapped["Subject"] = relationship(  # noqa: F821
        back_populates="curriculum_requirements"
    )
