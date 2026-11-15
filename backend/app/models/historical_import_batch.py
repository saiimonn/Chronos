import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

IMPORT_BATCH_STATUSES = ("staged", "committed", "rejected")


class HistoricalImportBatch(Base):
    """An uploaded Offered Courses workbook held for administrator review.

    The canonical bundle derived from the workbook is stored beside it.

    The exact uploaded bytes are kept, so confirmation imports what was reviewed
    and nothing else: `content_sha256` pins the batch, and a batch with any
    rejection can never be committed. Nothing reaches historical_snapshot or
    historical_record until an administrator confirms.

    A bundle too malformed to parse is still kept, as a `rejected` batch with
    one bundle-level rejection, so every refused attempt leaves an audit row.
    """

    __tablename__ = "historical_import_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staged', 'committed', 'rejected')",
            name="ck_historical_import_batch_status",
        ),
        # Content that is committed or awaiting review is refused rather than
        # staged again; batches that can never commit may be retried.
        Index(
            "uq_historical_import_batch_live_content",
            "content_sha256",
            unique=True,
            postgresql_where=text(
                "status = 'committed' OR (status = 'staged' AND rejected_count = 0)"
            ),
        ),
        CheckConstraint(
            "status <> 'committed' OR rejected_count = 0",
            name="ck_historical_import_batch_commit_clean",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="staged")
    # The stored files are deferred: summaries and detail never read them,
    # only confirmation does.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_records: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, deferred=True, deferred_group="files"
    )
    term_manifest: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, deferred=True, deferred_group="files"
    )
    source_manifest: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, deferred=True, deferred_group="files"
    )
    faculty_crosswalk: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, deferred=True, deferred_group="files"
    )
    # The uploaded Offered Courses workbook; the four files above are derived
    # from it. Null for batches staged from a canonical
    # bundle before uploads took the workbook.
    source_workbook: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, deferred=True, deferred_group="files"
    )
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Workbook rows listed as skipped; they never prevent confirmation.
    skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    populated_period_count: Mapped[int] = mapped_column(Integer, nullable=False)
    empty_period_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Null when the database checks could not run because no record was accepted.
    unknown_subject_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unknown_room_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Provenance only, like HistoricalSnapshot.approved_by_user_id.
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    committed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rejections: Mapped[list["HistoricalImportRejection"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="HistoricalImportRejection.ordinal",
    )

    @property
    def blocking_rejections(self) -> list["HistoricalImportRejection"]:
        return [item for item in self.rejections if item.blocking]

    @property
    def skipped_rows(self) -> list["HistoricalImportRejection"]:
        return [item for item in self.rejections if not item.blocking]


class HistoricalImportRejection(Base):
    """Why one workbook row or canonical record, or the batch, was refused.

    `file` is the workbook sheet for a workbook row. A row with `blocking`
    false was skipped (not history to import) and is listed for review only.

    `line` is null for a database-level refusal such as an unknown faculty ID
    or an already captured period. Raw row contents are not copied here.
    """

    __tablename__ = "historical_import_rejection"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "ordinal", name="uq_historical_import_rejection_ordinal"
        ),
    )

    rejection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("historical_import_batch.batch_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    file: Mapped[str] = mapped_column(String(40), nullable=False)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    # False for a workbook row that was skipped as not history to import.
    blocking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    batch: Mapped["HistoricalImportBatch"] = relationship(back_populates="rejections")
