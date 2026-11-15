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


class SubjectOffering(Base):
    """One term-specific offering of a catalog subject, such as CIS 1101 G1.

    Group No. is what users say and see; the UUID exists only because rows need
    a stable key that survives a subject code being reused. The number is
    assigned by the service, never by a client, and is unique within a term and
    subject so each term starts again at G1.

    Which Blocks an offering serves is not modelled yet — the association table
    arrives with the Block Section work — so the LAB/LEC block-count rule on
    `SubjectType.max_blocks_per_offering` is stated but not yet enforced here.
    """

    __tablename__ = "subject_offering"
    __table_args__ = (
        UniqueConstraint(
            "term_id",
            "subject_code",
            "group_no",
            name="uq_subject_offering_term_subject_group_no",
        ),
        CheckConstraint("group_no > 0", name="ck_subject_offering_group_no_positive"),
        # Redundant against the primary key, but `block_section_offering`
        # references the subject and term alongside the offering so a link
        # cannot name a different subject than the offering it points at, and
        # PostgreSQL requires a unique on every referenced column list.
        UniqueConstraint(
            "offering_id",
            "term_id",
            "subject_code",
            name="uq_subject_offering_id_term_subject",
        ),
    )

    offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Not separately indexed: the identity unique constraint is prefixed by
    # term_id, and every read is scoped to one term.
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("term.term_id", name="fk_subject_offering_term_id"),
        nullable=False,
    )
    subject_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("subject.subject_code", name="fk_subject_offering_subject_code"),
        nullable=False,
        index=True,
    )
    group_no: Mapped[int] = mapped_column(Integer, nullable=False)

    term: Mapped["Term"] = relationship(  # noqa: F821
        back_populates="subject_offerings"
    )
    subject: Mapped["Subject"] = relationship(  # noqa: F821
        back_populates="subject_offerings"
    )
    block_links: Mapped[list["BlockSectionOffering"]] = relationship(  # noqa: F821
        back_populates="offering", viewonly=True
    )

    @property
    def group_label(self) -> str:
        """The identifier a user reads, e.g. "CIS 1101 G1"."""
        return f"{self.subject_code} G{self.group_no}"
