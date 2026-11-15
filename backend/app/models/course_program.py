from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class CourseProgram(Base):
    """A degree program whose students are scheduled as cohorts, keyed by code."""

    __tablename__ = "course_program"

    program_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    program_name: Mapped[str] = mapped_column(String(150), nullable=False)
    # False for a program the department no longer runs. Kept so past Blocks
    # and curricula can name it; new Blocks cannot be created for it.
    # Server-side default only, so inserts written against older migrations do
    # not name it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    block_sections: Mapped[list["BlockSection"]] = relationship(  # noqa: F821
        back_populates="program",
        passive_deletes="all",
    )
    curricula: Mapped[list["Curriculum"]] = relationship(  # noqa: F821
        back_populates="program",
        passive_deletes="all",
    )
