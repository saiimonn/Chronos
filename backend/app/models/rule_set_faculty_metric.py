import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.extracted_rule import PATTERN_TYPE_SQL_LIST


class RuleSetFacultyMetric(Base):
    """One teacher's best candidate pattern in one stream of a rule set.

    Diagnostic only: the GA never reads this table. It records, for every
    teacher in a stream's training transactions, the candidate pattern that
    came closest to being retained and which thresholds it met, so the Rule
    Sets page can show why a teacher has rules or has none. Rows are written
    once, with the version, and never change.

    `source_faculty_key` is the identity the history used: the operational
    faculty ID for in-app mining, or the research key for an offline package.
    `faculty_id` links to the roster when that identity is known locally and
    stays empty otherwise. Subject and room codes are plain text, not foreign
    keys, because a teacher's best pattern may name a retired subject or room.
    """

    __tablename__ = "rule_set_faculty_metric"
    __table_args__ = (
        CheckConstraint(
            f"pattern_type IN ({PATTERN_TYPE_SQL_LIST})",
            name="ck_rule_set_faculty_metric_pattern_type",
        ),
        CheckConstraint(
            "support >= 0 AND support <= 1",
            name="ck_rule_set_faculty_metric_support_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rule_set_faculty_metric_confidence_range",
        ),
        CheckConstraint(
            "lift >= 0", name="ck_rule_set_faculty_metric_lift_non_negative"
        ),
        CheckConstraint(
            "term_recurrence >= 1 AND offering_count >= 1",
            name="ck_rule_set_faculty_metric_counts_positive",
        ),
        UniqueConstraint(
            "rule_set_id",
            "pattern_type",
            "source_faculty_key",
            name="uq_rule_set_faculty_metric_teacher",
        ),
    )

    metric_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pattern_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_faculty_key: Mapped[str] = mapped_column(String(64), nullable=False)
    faculty_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("faculty.faculty_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    faculty_name: Mapped[str] = mapped_column(String(150), nullable=False)
    subject_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    room_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    meeting_days: Mapped[str | None] = mapped_column(String(10), nullable=True)
    time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    offering_count: Mapped[int] = mapped_column(Integer, nullable=False)
    support: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    lift: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    term_recurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    meets_support: Mapped[bool] = mapped_column(Boolean, nullable=False)
    meets_confidence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    meets_lift: Mapped[bool] = mapped_column(Boolean, nullable=False)
    meets_term_recurrence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Whether this version persisted a rule for the teacher in this stream. A
    # pattern can meet every threshold and still have no rule when the catalog
    # no longer knows the teacher, subject or room.
    has_rule: Mapped[bool] = mapped_column(Boolean, nullable=False)
