"""CHR-120 / CHR-ING-005 staged historical uploads with review before import.

The administrator uploads the Offered Courses workbook; `offered_courses`
derives the canonical bundle from it using the local Terms, Subject catalog,
and Faculty roster. The bundle is validated with the CHR-108 parser and database checks, then held
as a batch. Nothing reaches the historical tables until an administrator confirms
the exact batch they reviewed; confirmation reuses the CHR-108 import.
Mining and Rule Set activation are never triggered from here.
"""

import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer_group

from app.core.errors import APIError
from app.models import HistoricalImportBatch, HistoricalImportRejection
from app.services.audit import record_event
from app.services.ingestion.historical_file import (
    BUNDLE_FILES,
    HistoricalImportData,
    HistoricalImportError,
    parse_historical_import,
)
from app.services.ingestion.offered_courses import (
    WORKBOOK_LABEL,
    OfferedCoursesBundle,
    WorkbookIssue,
    convert_offered_courses,
)
from app.services.ingestion.historical_import import (
    import_historical_data,
    validate_historical_import,
)

logger = logging.getLogger(__name__)

CROSSWALK_FILE = "faculty_crosswalk.csv"
COLUMNS = {
    "canonical_records.csv": "canonical_records",
    "term_manifest.csv": "term_manifest",
    "source_manifest.json": "source_manifest",
    CROSSWALK_FILE: "faculty_crosswalk",
}


def content_sha256(files: dict[str, bytes]) -> str:
    """One hash over all four files, each bound to its name."""
    digest = hashlib.sha256()
    for name in sorted(COLUMNS):
        digest.update(f"{name}:{hashlib.sha256(files[name]).hexdigest()}\n".encode())
    return digest.hexdigest()


def _parse(files: dict[str, bytes]) -> HistoricalImportData:
    return parse_historical_import(
        {name: files[name] for name in BUNDLE_FILES}, files[CROSSWALK_FILE]
    )


def _stored_files(batch: HistoricalImportBatch) -> dict[str, bytes]:
    return {name: getattr(batch, column) for name, column in COLUMNS.items()}


def _row_issues(
    issues: list[WorkbookIssue], *, blocking: bool, start: int
) -> list[HistoricalImportRejection]:
    return [
        HistoricalImportRejection(
            ordinal=start + index,
            file=issue.sheet[:40],
            line=issue.row,
            reason=issue.reason[:500],
            blocking=blocking,
        )
        for index, issue in enumerate(issues)
    ]


def stage_upload(
    session: Session, workbook: bytes, actor_id: UUID
) -> HistoricalImportBatch:
    """Convert an Offered Courses workbook and store it as a staged batch.

    Skipped rows are listed and never block; blocking rows, parser rejections,
    and database refusals keep the batch from being confirmed.
    """
    try:
        bundle = convert_offered_courses(session, workbook)
    except HistoricalImportError as error:
        content_hash = hashlib.sha256(workbook).hexdigest()
        batch = _reject_upload(session, workbook, None, content_hash, [str(error)], actor_id)
        raise _invalid_upload(batch, str(error)) from error

    files = bundle.files
    content_hash = content_sha256(files)
    _refuse_existing(session, content_hash)
    blocking = _row_issues(bundle.blocking, blocking=True, start=0)
    skipped = _row_issues(bundle.skipped, blocking=False, start=len(blocking))

    if not bundle.accepted_count:
        reason = (
            f"{WORKBOOK_LABEL}: no row can be imported"
            + ("; fix the blocking rows and upload again" if blocking else "")
        )
        batch = _reject_upload(
            session, workbook, bundle, content_hash, [] if blocking else [reason],
            actor_id, rows=blocking + skipped,
        )
        raise _invalid_upload(batch, reason)

    try:
        data = _parse(files)
    except HistoricalImportError as error:
        batch = _reject_upload(
            session, workbook, bundle, content_hash, [str(error)], actor_id,
            rows=blocking + skipped,
        )
        raise _invalid_upload(batch, str(error)) from error

    rejections = blocking + [
        HistoricalImportRejection(
            ordinal=len(blocking) + len(skipped) + index,
            file=item.file,
            line=item.line,
            reason=item.reason[:500],
        )
        for index, item in enumerate(data.rejections)
    ]
    unknown_subjects = unknown_rooms = None
    try:
        validation = validate_historical_import(session, data)
        unknown_subjects = validation.unknown_subject_count
        unknown_rooms = validation.unknown_room_count
    except HistoricalImportError as error:
        rejections.append(
            HistoricalImportRejection(
                ordinal=len(rejections) + len(skipped),
                file="database",
                line=None,
                reason=str(error)[:500],
            )
        )

    batch = HistoricalImportBatch(
        status="staged",
        content_sha256=content_hash,
        **{column: files[name] for name, column in COLUMNS.items()},
        source_workbook=workbook,
        accepted_count=len(data.records),
        rejected_count=len(rejections),
        skipped_count=len(skipped),
        populated_period_count=len(data.periods),
        empty_period_count=len(data.empty_periods),
        unknown_subject_count=unknown_subjects,
        unknown_room_count=unknown_rooms,
        uploaded_by_user_id=actor_id,
        rejections=rejections + skipped,
    )
    session.add(batch)
    try:
        session.flush()
        record_event(
            session,
            actor_id=actor_id,
            operation="historical_upload.stage",
            target_type="historical_import_batch",
            target_id=batch.batch_id,
            details={
                "content_sha256": content_hash,
                "source_workbook_sha256": bundle.workbook_sha256,
                "status": batch.status,
                "accepted_count": batch.accepted_count,
                "rejected_count": batch.rejected_count,
                "skipped_count": batch.skipped_count,
                "populated_period_count": batch.populated_period_count,
                "empty_period_count": batch.empty_period_count,
            },
        )
        session.commit()
    except IntegrityError as error:
        # A concurrent identical upload won the content-hash race.
        session.rollback()
        _refuse_existing(session, content_hash, error)
        raise
    logger.info(
        "historical_upload_staged actor_id=%s batch_id=%s accepted=%s rejected=%s skipped=%s",
        actor_id,
        batch.batch_id,
        batch.accepted_count,
        batch.rejected_count,
        batch.skipped_count,
    )
    return batch


def _reject_upload(
    session: Session,
    workbook: bytes,
    bundle: OfferedCoursesBundle | None,
    content_hash: str,
    reasons: list[str],
    actor_id: UUID,
    *,
    rows: list[HistoricalImportRejection] | None = None,
) -> HistoricalImportBatch:
    """Keep an upload that cannot be staged as a rejected batch for audit."""
    rows = rows or []
    bundle_rows = [
        HistoricalImportRejection(
            ordinal=len(rows) + index, file="workbook", line=None, reason=reason[:500]
        )
        for index, reason in enumerate(reasons)
    ]
    files = bundle.files if bundle is not None else dict.fromkeys(COLUMNS, b"")
    batch = HistoricalImportBatch(
        status="rejected",
        content_sha256=content_hash,
        **{column: files[name] for name, column in COLUMNS.items()},
        source_workbook=workbook,
        accepted_count=0,
        rejected_count=sum(1 for row in rows if row.blocking is not False) + len(bundle_rows),
        skipped_count=sum(1 for row in rows if row.blocking is False),
        populated_period_count=0,
        empty_period_count=0,
        uploaded_by_user_id=actor_id,
        rejections=rows + bundle_rows,
    )
    session.add(batch)
    try:
        session.commit()
    except IntegrityError as error:
        # A concurrent identical upload won the content-hash race.
        session.rollback()
        _refuse_existing(session, content_hash, error)
        raise
    logger.info(
        "historical_upload_rejected actor_id=%s batch_id=%s", actor_id, batch.batch_id
    )
    return batch


def _refuse_existing(
    session: Session, content_hash: str, cause: Exception | None = None
) -> None:
    """Refuse content that is committed or staged clean; earlier failed attempts
    stay as audit rows and the content is validated afresh against the catalog."""
    existing = session.execute(
        select(HistoricalImportBatch.batch_id, HistoricalImportBatch.status).where(
            HistoricalImportBatch.content_sha256 == content_hash,
            or_(
                HistoricalImportBatch.status == "committed",
                and_(
                    HistoricalImportBatch.status == "staged",
                    HistoricalImportBatch.rejected_count == 0,
                ),
            ),
        )
    ).first()
    if existing is not None:
        batch_id, status = existing
        action = (
            "It was already imported."
            if status == "committed"
            else "Confirm it from the upload list; if it no longer matches the "
            "database, confirm will say it is stale."
        )
        raise APIError(
            409,
            "HISTORY_UPLOAD_DUPLICATE",
            f"This exact content is already {status} as batch {batch_id}. {action}",
            {"batch_id": str(batch_id)},
        ) from cause


def _invalid_upload(batch: HistoricalImportBatch, reason: str) -> APIError:
    return APIError(
        422, "HISTORY_UPLOAD_INVALID", reason, {"batch_id": str(batch.batch_id)}
    )


def list_batches(
    session: Session, *, limit: int = 50, offset: int = 0
) -> list[HistoricalImportBatch]:
    return list(
        session.scalars(
            select(HistoricalImportBatch)
            .order_by(
                HistoricalImportBatch.uploaded_at.desc(),
                HistoricalImportBatch.batch_id,
            )
            .limit(limit)
            .offset(offset)
        )
    )


def get_batch(session: Session, batch_id: UUID) -> HistoricalImportBatch:
    batch = session.get(HistoricalImportBatch, batch_id)
    if batch is None:
        raise APIError(404, "NOT_FOUND", "The requested upload batch was not found.")
    return batch


def confirm_batch(
    session: Session, batch_id: UUID, reviewed_sha256: str, actor_id: UUID
) -> tuple[HistoricalImportBatch, tuple[UUID, ...]]:
    """Import the reviewed batch atomically, or refuse with nothing written."""
    batch = session.scalars(
        select(HistoricalImportBatch)
        .where(HistoricalImportBatch.batch_id == batch_id)
        .options(undefer_group("files"))
        .with_for_update()
    ).one_or_none()
    if batch is None:
        raise APIError(404, "NOT_FOUND", "The requested upload batch was not found.")
    if batch.status == "rejected":
        raise APIError(
            409,
            "HISTORY_UPLOAD_HAS_REJECTIONS",
            "This upload was rejected and cannot be imported. Correct the source and upload it again.",
        )
    if batch.status != "staged":
        raise APIError(
            409, "HISTORY_UPLOAD_NOT_STAGED", "This upload batch was already imported."
        )
    if reviewed_sha256 != batch.content_sha256:
        raise APIError(
            409,
            "HISTORY_UPLOAD_STALE",
            "The batch differs from the one reviewed. Reload it and review again.",
        )
    if batch.rejected_count:
        raise APIError(
            409,
            "HISTORY_UPLOAD_HAS_REJECTIONS",
            "A batch with rejected records cannot be imported. Correct the source and upload it again.",
        )
    files = _stored_files(batch)
    try:
        if content_sha256(files) != batch.content_sha256:
            raise HistoricalImportError("stored batch content no longer matches its hash")
        data = _parse(files)
        if data.rejections:
            raise HistoricalImportError("stored batch no longer validates")
        snapshot_ids = import_historical_data(session, data)
    except HistoricalImportError as error:
        session.rollback()
        logger.warning(
            "historical_upload_confirm_refused actor_id=%s batch_id=%s reason=%s",
            actor_id,
            batch_id,
            error,
        )
        raise APIError(
            409,
            "HISTORY_UPLOAD_STALE",
            f"The batch no longer matches the database: {error}",
        ) from error
    batch.status = "committed"
    batch.committed_by_user_id = actor_id
    batch.committed_at = datetime.now(timezone.utc)
    record_event(
        session,
        actor_id=actor_id,
        operation="historical_upload.confirm",
        target_type="historical_import_batch",
        target_id=batch.batch_id,
        details={
            "content_sha256": batch.content_sha256,
            "accepted_count": batch.accepted_count,
            "snapshot_ids": snapshot_ids,
            "before": {"status": "staged"},
            "after": {"status": "committed"},
        },
    )
    session.commit()
    logger.info(
        "historical_upload_committed actor_id=%s batch_id=%s snapshots=%s",
        actor_id,
        batch_id,
        len(snapshot_ids),
    )
    return batch, snapshot_ids
