from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.models import BlockSection, BlockSectionOffering
from app.schemas.block import (
    BlockClassCreate,
    BlockClassResponse,
    BlockCreate,
    BlockResponse,
    BlockUpdate,
)
from app.schemas.common import ListResponse
from app.services.catalog import blocks as service

router = APIRouter(
    prefix="/block-sections",
    tags=["block sections"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[BlockResponse])
def list_blocks(db: DbSession, term_id: Annotated[UUID, Query()]) -> dict:
    return {"items": service.list_blocks(db, term_id)}


@router.post("", response_model=BlockResponse, status_code=status.HTTP_201_CREATED)
def create_block(payload: BlockCreate, db: DbSession) -> BlockSection:
    return service.create_block(db, payload)


@router.get("/{block_id}", response_model=BlockResponse)
def read_block(block_id: UUID, db: DbSession) -> BlockSection:
    return service.get_block(db, block_id)


@router.patch("/{block_id}", response_model=BlockResponse)
def update_block(block_id: UUID, payload: BlockUpdate, db: DbSession) -> BlockSection:
    return service.update_block(db, block_id, payload)


@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_block(block_id: UUID, db: DbSession) -> None:
    service.delete_block(db, block_id)


@router.get("/{block_id}/classes", response_model=ListResponse[BlockClassResponse])
def list_classes(block_id: UUID, db: DbSession) -> dict:
    return {"items": service.list_classes(db, block_id)}


@router.post(
    "/{block_id}/classes",
    response_model=BlockClassResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_class(
    block_id: UUID, payload: BlockClassCreate, db: DbSession
) -> BlockSectionOffering:
    return service.add_class(db, block_id, payload)


@router.delete(
    "/{block_id}/classes/{class_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_class(block_id: UUID, class_id: UUID, db: DbSession) -> None:
    service.remove_class(db, block_id, class_id)
