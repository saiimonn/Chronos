import binascii
import hashlib
from base64 import b64decode
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_department_administrator
from app.core.errors import APIError
from app.models.app_user import AppUser
from app.schemas.common import ListResponse
from app.schemas.historical_upload import (
    HistoricalUploadBatchDetailResponse,
    HistoricalUploadBatchResponse,
    HistoricalUploadCommitResponse,
    HistoricalUploadConfirmRequest,
    HistoricalUploadRequest,
)
from app.services.audit import audit_rejections
from app.services.ingestion import historical_upload as service
from app.services.ingestion.offered_courses import (
    TEMPLATE_FILENAME,
    WORKBOOK_LABEL,
    build_template,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter(
    prefix="/historical-uploads",
    tags=["historical-uploads"],
    dependencies=[Depends(require_department_administrator)],
)

DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[AppUser, Depends(require_department_administrator)]


def _decode(payload: HistoricalUploadRequest) -> bytes:
    try:
        return b64decode(payload.workbook, validate=True)
    except (binascii.Error, ValueError) as error:
        raise APIError(
            422, "HISTORY_UPLOAD_INVALID", f"{WORKBOOK_LABEL}: invalid base64 content"
        ) from error


@router.get("", response_model=ListResponse[HistoricalUploadBatchResponse])
def list_uploads(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return {"items": service.list_batches(db, limit=limit, offset=offset)}


@router.post(
    "",
    response_model=HistoricalUploadBatchDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload(payload: HistoricalUploadRequest, db: DbSession, user: Admin):
    details: dict = {}
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="historical_upload.stage",
        target_type="historical_import_batch",
        details=details,
    ):
        workbook = _decode(payload)
        # Hash and size only: the workbook itself holds raw historical data.
        details["source_workbook_sha256"] = hashlib.sha256(workbook).hexdigest()
        details["file_bytes"] = len(workbook)
        return service.stage_upload(db, workbook, user.user_id)


@router.get("/template")
def download_template() -> Response:
    """The Offered Courses format to fill in: the columns, sheet naming, and rules."""
    return Response(
        content=build_template(),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{batch_id}", response_model=HistoricalUploadBatchDetailResponse)
def read_upload(batch_id: UUID, db: DbSession):
    return service.get_batch(db, batch_id)


@router.post("/{batch_id}/confirm", response_model=HistoricalUploadCommitResponse)
def confirm_upload(
    batch_id: UUID, payload: HistoricalUploadConfirmRequest, db: DbSession, user: Admin
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="historical_upload.confirm",
        target_type="historical_import_batch",
        target_id=batch_id,
        details={"reviewed_sha256": payload.content_sha256},
    ):
        batch, snapshot_ids = service.confirm_batch(
            db, batch_id, payload.content_sha256, user.user_id
        )
    return {"batch": batch, "snapshot_ids": list(snapshot_ids)}
