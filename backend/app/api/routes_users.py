from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_department_administrator
from app.models import AppUser
from app.schemas.auth import ManagedUserResponse, PasswordReset, UserCreate, UserUpdate
from app.schemas.common import ListResponse
from app.services import users as service
from app.services.audit import audit_rejections

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_department_administrator)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[ManagedUserResponse])
def list_users(db: DbSession) -> dict:
    return {"items": service.list_users(db)}


@router.post(
    "", response_model=ManagedUserResponse, status_code=status.HTTP_201_CREATED
)
def create_user(payload: UserCreate, db: DbSession) -> AppUser:
    return service.create_user(db, payload)


# There is no delete: an account is deactivated, which keeps its history.
@router.patch("/{user_id}", response_model=ManagedUserResponse)
def update_user(user_id: UUID, payload: UserUpdate, db: DbSession) -> AppUser:
    return service.update_user(db, user_id, payload)


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: UUID,
    payload: PasswordReset,
    db: DbSession,
    actor: Annotated[AppUser, Depends(require_department_administrator)],
) -> None:
    with audit_rejections(
        db,
        actor_id=actor.user_id,
        operation="user.password_reset",
        target_type="user",
        target_id=user_id,
    ):
        service.reset_password(db, actor.user_id, user_id, payload.new_password)
