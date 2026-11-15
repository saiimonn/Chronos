import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# The four ARM streams. Faculty is the antecedent of the first three; the fourth
# is mined as faculty + subject -> room, so its rows carry both consequent
# columns and its confidence is conditioned on the subject.
PATTERN_TYPES = (
    "teacher_subject",
    "teacher_day",
    "teacher_time",
    "teacher_subject_room",
)
PATTERN_TYPE_SQL_LIST = ", ".join(f"'{p}'" for p in PATTERN_TYPES)

# Exactly the consequent columns its stream defines, and no others.
_CONSEQUENT_MATCHES_PATTERN = """
    (pattern_type = 'teacher_subject'
        AND subject_code IS NOT NULL
        AND room_code IS NULL
        AND meeting_days IS NULL
        AND time_slot IS NULL)
    OR (pattern_type = 'teacher_day'
        AND meeting_days IS NOT NULL
        AND subject_code IS NULL
        AND room_code IS NULL
        AND time_slot IS NULL)
    OR (pattern_type = 'teacher_time'
        AND time_slot IS NOT NULL
        AND subject_code IS NULL
        AND room_code IS NULL
        AND meeting_days IS NULL)
    OR (pattern_type = 'teacher_subject_room'
        AND subject_code IS NOT NULL
        AND room_code IS NOT NULL
        AND meeting_days IS NULL
        AND time_slot IS NULL)
"""


class ExtractedRule(Base):
    """An association rule mined from historical schedule records.

    `pattern_type` decides which consequent columns carry the rule's payload:

    - ``teacher_subject``      -> subject_code
    - ``teacher_day``          -> meeting_days
    - ``teacher_time``         -> time_slot
    - ``teacher_subject_room`` -> room_code, given subject_code in the antecedent

    A rule belongs to the mining run that produced it, not to a single academic
    term: `term_recurrence` counts how many terms it reappeared in, and the
    terms themselves hang off ``RuleSet.included_terms``.

    `guidance_score` is the single number the GA fitness function reads; the raw
    ARM metrics are kept so a rule can be re-weighted or explained without
    re-running the mining step. It is soft guidance only and never decides
    whether a schedule is valid.
    """

    __tablename__ = "extracted_rule"
    __table_args__ = (
        CheckConstraint(
            f"pattern_type IN ({PATTERN_TYPE_SQL_LIST})",
            name="ck_extracted_rule_pattern_type",
        ),
        CheckConstraint(
            _CONSEQUENT_MATCHES_PATTERN,
            name="ck_extracted_rule_consequent_matches_pattern",
        ),
        CheckConstraint(
            "support > 0 AND support <= 1", name="ck_extracted_rule_support_range"
        ),
        CheckConstraint(
            "confidence > 0 AND confidence <= 1",
            name="ck_extracted_rule_confidence_range",
        ),
        CheckConstraint("lift >= 0", name="ck_extracted_rule_lift_non_negative"),
        CheckConstraint(
            "term_recurrence >= 1", name="ck_extracted_rule_term_recurrence_positive"
        ),
        CheckConstraint(
            "guidance_score >= 0 AND guidance_score <= 1",
            name="ck_extracted_rule_guidance_score_range",
        ),
        # One row per distinct pattern per mining run. NULL consequents must
        # compare equal here or a re-run would slip duplicates past the
        # constraint, so the index is declared NULLS NOT DISTINCT.
        UniqueConstraint(
            "rule_set_id",
            "pattern_type",
            "faculty_id",
            "subject_code",
            "room_code",
            "meeting_days",
            "time_slot",
            name="uq_extracted_rule_pattern",
            postgresql_nulls_not_distinct=True,
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "rule_set.rule_set_id",
            name="fk_extracted_rule_rule_set_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    # One of PATTERN_TYPES.
    pattern_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    faculty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faculty.faculty_id"), nullable=False, index=True
    )
    # Consequents. All nullable; the pattern CHECK above decides which apply.
    subject_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("subject.subject_code"), nullable=True, index=True
    )
    room_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("room.room_code"), nullable=True, index=True
    )
    # Day pattern such as "MWF" or "TTh"; mirrors ScheduleRecord.meeting_days.
    meeting_days: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Slot label such as "07:30-08:30"; mirrors ScheduleRecord.time_slot.
    time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)

    support: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    lift: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    # How many distinct terms the pattern reappears in.
    term_recurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Normalized to 0-1 so the streams stay comparable to one another.
    guidance_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    rule_set: Mapped["RuleSet"] = relationship(  # noqa: F821
        back_populates="extracted_rules"
    )
    faculty: Mapped["Faculty"] = relationship(  # noqa: F821
        back_populates="extracted_rules"
    )
    subject: Mapped["Subject | None"] = relationship(  # noqa: F821
        back_populates="extracted_rules"
    )
    room: Mapped["Room | None"] = relationship(  # noqa: F821
        back_populates="extracted_rules"
    )
