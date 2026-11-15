import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.extracted_rule import PATTERN_TYPE_SQL_LIST

RULE_SET_STATUSES = ("draft", "active", "superseded")

# Which academic terms a mining run drew its records from. The paper records
# these per rule set, not per rule, because one rule may recur across terms.
rule_set_term = Table(
    "rule_set_term",
    Base.metadata,
    Column(
        "rule_set_id",
        UUID(as_uuid=True),
        ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "term_id",
        UUID(as_uuid=True),
        ForeignKey("term.term_id"),
        primary_key=True,
    ),
)


# The immutable historical snapshots a mining run drew from. Unlike
# rule_set_term, these rows and their periods can never change after capture.
rule_set_source_snapshot = Table(
    "rule_set_source_snapshot",
    Base.metadata,
    Column(
        "rule_set_id",
        UUID(as_uuid=True),
        ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "snapshot_id",
        UUID(as_uuid=True),
        ForeignKey("historical_snapshot.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    ),
)


class RuleSet(Base):
    """One versioned output of a rule-mining run.

    A new set is never activated automatically: it lands as ``draft`` and the
    Department Administrator promotes it once the mining summary has been
    reviewed. The previously active set stays active until then, and superseded
    sets are kept so a schedule generated in the past can be explained or a bad
    refresh rolled back.
    """

    __tablename__ = "rule_set"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_rule_set_status",
        ),
        UniqueConstraint("version", name="uq_rule_set_version"),
        # At most one set may guide the GA at a time.
        Index(
            "uq_rule_set_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_rule_set_job_id",
            "job_id",
            unique=True,
            postgresql_where=text("job_id IS NOT NULL"),
        ),
    )

    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_job.job_id", name="fk_rule_set_job_id"),
        nullable=True,
    )
    # Monotonic; the GA and the audit trail refer to a set by this number.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Optional display name such as "1.1" for a version that re-publishes an
    # earlier version's rules. ``version`` stays the monotonic identity.
    version_label: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Set when the administrator promotes the version to ``active``.
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Only recovery needs the full response, not metadata or guidance reads.
    publication_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, deferred=True
    )
    # The ARM job and administrator that produced this version. Provenance
    # only, like historical_snapshot.approved_by_user_id: not foreign keys, so
    # removing operational rows cannot rewrite where a version came from.
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # What the run counted, excluded and actually persisted, per stream.
    mining_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    thresholds: Mapped[list["RuleSetThreshold"]] = relationship(
        back_populates="rule_set", cascade="all, delete-orphan"
    )
    extracted_rules: Mapped[list["ExtractedRule"]] = relationship(  # noqa: F821
        back_populates="rule_set", cascade="all, delete-orphan"
    )
    included_terms: Mapped[list["Term"]] = relationship(  # noqa: F821
        secondary=rule_set_term, back_populates="rule_sets"
    )
    source_snapshots: Mapped[list["HistoricalSnapshot"]] = relationship(  # noqa: F821
        secondary=rule_set_source_snapshot
    )


class RuleSetThreshold(Base):
    """The Apriori filter settings one rule set applied to one pattern stream.

    Stored per stream rather than per set because the streams do not share a
    starting point: teacher-subject rules may draw on records from 2018, while
    teacher-day, teacher-time, and teacher-subject-room rules are restricted to
    2023 onward, where the room inventory and trimestral calendar match current
    conditions.
    """

    __tablename__ = "rule_set_threshold"
    __table_args__ = (
        CheckConstraint(
            f"pattern_type IN ({PATTERN_TYPE_SQL_LIST})",
            name="ck_rule_set_threshold_pattern_type",
        ),
        CheckConstraint(
            "min_support > 0 AND min_support <= 1",
            name="ck_rule_set_threshold_min_support_range",
        ),
        CheckConstraint(
            "min_confidence > 0 AND min_confidence <= 1",
            name="ck_rule_set_threshold_min_confidence_range",
        ),
        CheckConstraint(
            "min_lift >= 0", name="ck_rule_set_threshold_min_lift_non_negative"
        ),
        CheckConstraint(
            "min_term_recurrence >= 1",
            name="ck_rule_set_threshold_min_term_recurrence_positive",
        ),
        UniqueConstraint(
            "rule_set_id", "pattern_type", name="uq_rule_set_threshold_stream"
        ),
    )

    threshold_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_set.rule_set_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pattern_type: Mapped[str] = mapped_column(String(30), nullable=False)
    min_support: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    min_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    min_lift: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    min_term_recurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Oldest academic year this stream was allowed to mine, e.g. "2018-2019".
    earliest_academic_year: Mapped[str] = mapped_column(String(9), nullable=False)

    rule_set: Mapped["RuleSet"] = relationship(back_populates="thresholds")
