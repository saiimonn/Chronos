from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_department_administrator
from app.models.app_user import AppUser
from app.schemas.common import ListResponse
from app.schemas.rule_set import (
    HistoricalTermOptionResponse,
    RuleSetDetailResponse,
    RuleSetFacultyCoverage,
    RuleSetListResponse,
    RuleSetRulePage,
)
from app.services import rule_sets as service
from app.services.audit import audit_rejections

# Mirrors app.models.extracted_rule.PATTERN_TYPES.
PatternTypeParam = Literal[
    "teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"
]

router = APIRouter(
    prefix="/rule-sets",
    tags=["rule-sets"],
    dependencies=[Depends(require_department_administrator)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListResponse[RuleSetListResponse])
def list_rule_sets(db: DbSession) -> dict:
    return {"items": service.list_rule_sets(db)}


# Declared before /{rule_set_id} so the literal path wins the match.
@router.get(
    "/historical-terms", response_model=ListResponse[HistoricalTermOptionResponse]
)
def list_historical_terms(db: DbSession) -> dict:
    return {"items": service.list_historical_terms(db)}


@router.get("/{rule_set_id}", response_model=RuleSetDetailResponse)
def read_rule_set(rule_set_id: UUID, db: DbSession) -> dict:
    return service.get_rule_set_detail(db, rule_set_id)


@router.get("/{rule_set_id}/rules", response_model=RuleSetRulePage)
def list_rule_set_rules(
    rule_set_id: UUID,
    db: DbSession,
    pattern_type: Annotated[PatternTypeParam, Query()],
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    faculty_id: Annotated[UUID | None, Query()] = None,
) -> dict:
    return service.list_rule_set_rules(
        db,
        rule_set_id,
        pattern_type,
        order=order,
        limit=limit,
        offset=offset,
        faculty_id=faculty_id,
    )


@router.get("/{rule_set_id}/faculty-coverage", response_model=RuleSetFacultyCoverage)
def read_rule_set_faculty_coverage(
    rule_set_id: UUID,
    db: DbSession,
    pattern_type: Annotated[PatternTypeParam, Query()],
) -> dict:
    return service.list_rule_set_faculty_coverage(db, rule_set_id, pattern_type)


@router.post("/{rule_set_id}/activate", response_model=RuleSetDetailResponse)
def activate_rule_set(
    rule_set_id: UUID,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="rule_set.activate",
        target_type="rule_set",
        target_id=rule_set_id,
    ):
        return service.activate_rule_set(db, rule_set_id, actor_id=user.user_id)


@router.post("/{rule_set_id}/rollback", response_model=RuleSetDetailResponse)
def rollback_rule_set(
    rule_set_id: UUID,
    db: DbSession,
    user: Annotated[AppUser, Depends(require_department_administrator)],
) -> dict:
    with audit_rejections(
        db,
        actor_id=user.user_id,
        operation="rule_set.rollback",
        target_type="rule_set",
        target_id=rule_set_id,
    ):
        return service.rollback_rule_set(db, rule_set_id, actor_id=user.user_id)
