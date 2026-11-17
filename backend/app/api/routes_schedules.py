from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_committee_member
from app.models import AppUser, ScheduleType, UserRole
from app.schemas.common import ListResponse
from app.schemas.schedule import (
    AssignmentUpdate,
    BlockReplacementConfirmRequest,
    BlockReplacementPreviewRequest,
    BlockReplacementPreviewResponse,
    MasterScheduleCreate,
    ScheduleLifecycleResponse,
    ScheduleResponse,
    TimetableProjectionResponse,
    ValidationResponse,
)
from app.schemas.schedule_metrics import ScheduleMetricsResponse
from app.services import schedules as service
from app.services.audit import audit_rejections


router = APIRouter(
    prefix="/schedules",
    tags=["schedules"],
    dependencies=[Depends(require_committee_member)],
)
DbSession = Annotated[Session, Depends(get_db)]
User = Annotated[AppUser, Depends(get_current_user)]


@router.get("", response_model=ListResponse[ScheduleResponse])
def list_schedules(
    db: DbSession,
    term_id: Annotated[UUID | None, Query()] = None,
    schedule_type: Annotated[Literal["routine", "master"] | None, Query()] = None,
) -> dict:
    return {
        "items": [
            service.serialize_schedule(schedule)
            for schedule in service.list_schedules(
                db,
                term_id,
                ScheduleType(schedule_type) if schedule_type is not None else None,
            )
        ]
    }


@router.post("/master", response_model=ScheduleResponse, status_code=201)
def create_master_schedule(
    payload: MasterScheduleCreate, db: DbSession, user: User
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.master_create",
        target_type="schedule",
        details={"term_id": payload.term_id},
    ):
        schedule = service.create_master_schedule(
            db, payload.term_id, actor_id=user.user_id
        )
    return service.serialize_schedule(schedule)


@router.get("/master", response_model=ScheduleResponse)
def read_master_schedule(term_id: Annotated[UUID, Query()], db: DbSession) -> dict:
    return service.serialize_schedule(service.get_master_schedule(db, term_id))


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def read_schedule(schedule_id: UUID, db: DbSession) -> dict:
    return service.serialize_schedule(service.get_schedule(db, schedule_id))


@router.get(
    "/{schedule_id}/timetable", response_model=TimetableProjectionResponse
)
def read_timetable_projection(
    schedule_id: UUID,
    term_id: Annotated[UUID, Query()],
    db: DbSession,
    block_id: Annotated[UUID | None, Query()] = None,
    room_code: Annotated[
        str | None, Query(min_length=1, max_length=20, pattern=r".*\S.*")
    ] = None,
    faculty_id: Annotated[UUID | None, Query()] = None,
) -> dict:
    return service.get_timetable_projection(
        db,
        schedule_id,
        term_id,
        block_id=block_id,
        room_code=room_code,
        faculty_id=faculty_id,
    )


@router.patch(
    "/{schedule_id}/assignments/{assignment_id}",
    response_model=ScheduleLifecycleResponse,
)
def update_assignment(
    schedule_id: UUID,
    assignment_id: UUID,
    payload: AssignmentUpdate,
    db: DbSession,
    user: User,
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.assignment_update",
        target_type="schedule",
        target_id=schedule_id,
        details={"assignment_id": assignment_id},
    ):
        schedule, validation = service.update_assignment(
            db, schedule_id, assignment_id, payload, actor_id=user.user_id
        )
    return {
        "schedule": service.serialize_schedule(schedule),
        "validation": service.serialize_validation(validation),
    }


@router.get("/{schedule_id}/metrics", response_model=ScheduleMetricsResponse)
def read_schedule_metrics(schedule_id: UUID, db: DbSession) -> dict:
    """Quality and resource-utilization metrics of one schedule (read-only)."""
    return service.schedule_metrics(db, schedule_id)


@router.post("/{schedule_id}/validate", response_model=ValidationResponse)
def validate_schedule(schedule_id: UUID, db: DbSession) -> dict:
    return service.serialize_validation(service.validate_schedule(db, schedule_id))


@router.post("/{schedule_id}/finalize", response_model=ScheduleLifecycleResponse)
def finalize_schedule(schedule_id: UUID, db: DbSession, user: User) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.finalize",
        target_type="schedule",
        target_id=schedule_id,
    ):
        schedule, validation = service.finalize_schedule(
            db, schedule_id, actor_id=user.user_id
        )
    return {
        "schedule": service.serialize_schedule(schedule),
        "validation": service.serialize_validation(validation),
    }


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: UUID, db: DbSession, user: User) -> Response:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.delete",
        target_type="schedule",
        target_id=schedule_id,
    ):
        service.delete_schedule(
            db,
            schedule_id,
            actor_id=user.user_id,
            allow_finalized=user.role == UserRole.DEPARTMENT_ADMINISTRATOR.value,
        )
    return Response(status_code=204)


@router.get("/{schedule_id}/export")
def export_schedule(
    schedule_id: UUID,
    db: DbSession,
    user: User,
    format: Annotated[Literal["csv", "xlsx", "pdf"], Query()] = "csv",
    block_id: Annotated[UUID | None, Query()] = None,
    room_code: Annotated[
        str | None, Query(min_length=1, max_length=20, pattern=r".*\S.*")
    ] = None,
    faculty_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.export",
        target_type="schedule",
        target_id=schedule_id,
        details={"format": format, "block_id": block_id, "room_code": room_code,
            "faculty_id": faculty_id,
        },
    ):
        filename, media_type, content = service.export_schedule(
            db,
            schedule_id,
            format,
            block_id=block_id,
            room_code=room_code,
            faculty_id=faculty_id,
            actor_id=user.user_id,
        )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{schedule_id}/blocks/{block_id}/preview",
    response_model=BlockReplacementPreviewResponse,
)
@router.post(
    "/{schedule_id}/blocks/{block_id}/replacement/preview",
    response_model=BlockReplacementPreviewResponse,
    include_in_schema=False,
)
def preview_block_replacement(
    schedule_id: UUID,
    block_id: UUID,
    payload: BlockReplacementPreviewRequest,
    db: DbSession,
) -> dict:
    return service.preview_block_replacement(db, schedule_id, block_id, payload)


@router.post(
    "/{schedule_id}/blocks/{block_id}/confirm",
    response_model=ScheduleLifecycleResponse,
)
@router.post(
    "/{schedule_id}/blocks/{block_id}/replacement/confirm",
    response_model=ScheduleLifecycleResponse,
    include_in_schema=False,
)
def confirm_block_replacement(
    schedule_id: UUID,
    block_id: UUID,
    payload: BlockReplacementConfirmRequest,
    db: DbSession,
    user: User,
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="schedule.block_replace",
        target_type="schedule",
        target_id=schedule_id,
        details={"block_id": block_id},
    ):
        schedule, validation = service.confirm_block_replacement(
            db, schedule_id, block_id, payload, actor_id=user.user_id
        )
    return {
        "schedule": service.serialize_schedule(schedule),
        "validation": service.serialize_validation(validation),
    }


def _block_lock_route(operation: str, action):
    def handler(schedule_id: UUID, block_id: UUID, db: DbSession, user: User) -> dict:
        with audit_rejections(
            db,
            actor_id=user.user_id,
            operation=operation,
            target_type="schedule",
            target_id=schedule_id,
            details={"block_id": block_id},
        ):
            schedule, validation = action(
                db, schedule_id, block_id, actor_id=user.user_id
            )
        return {
            "schedule": service.serialize_schedule(schedule),
            "validation": service.serialize_validation(validation),
        }

    return handler


# Approving a Block locks it; these open it for editing, lock it again, or
# withdraw its unlocked week so it (and its pair) can be generated afresh.
router.add_api_route(
    "/{schedule_id}/blocks/{block_id}/unlock",
    _block_lock_route("schedule.block_unlock", service.unlock_block),
    methods=["POST"],
    response_model=ScheduleLifecycleResponse,
    name="unlock_block",
)
router.add_api_route(
    "/{schedule_id}/blocks/{block_id}/lock",
    _block_lock_route("schedule.block_lock", service.lock_block),
    methods=["POST"],
    response_model=ScheduleLifecycleResponse,
    name="lock_block",
)
router.add_api_route(
    "/{schedule_id}/blocks/{block_id}/clear",
    _block_lock_route("schedule.block_clear", service.clear_block),
    methods=["POST"],
    response_model=ScheduleLifecycleResponse,
    name="clear_block",
)
