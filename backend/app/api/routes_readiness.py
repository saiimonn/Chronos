from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.core.errors import APIError
from app.repositories.protocols import SchedulingProblemNotFoundError
from app.repositories.scheduling import SQLAlchemySchedulingProblemProvider
from app.schemas.readiness import ReadinessResponse
from app.services.catalog.curriculum import load_cohort_coverage
from app.services.curriculum_readiness import analyze_curriculum_coverage
from app.services.readiness import ReadinessBlocker, analyze_readiness

router = APIRouter(
    prefix="/terms",
    tags=["readiness"],
    dependencies=[Depends(require_committee_member)],
)


@router.get("/{term_id}/readiness", response_model=ReadinessResponse)
def read_readiness(term_id: UUID, db: Annotated[Session, Depends(get_db)]) -> dict:
    # Readiness is asked of the same assembled problem the GA will consume, so
    # a clear report and a runnable input cannot drift apart.
    provider = SQLAlchemySchedulingProblemProvider(db)
    try:
        problem = provider.get_problem(term_id)
    except SchedulingProblemNotFoundError as error:
        raise APIError(404, "NOT_FOUND", "The requested term was not found.") from error
    # CHR-235: availability review is optional and never blocks; an unset mask
    # is open in every slot, so the problem alone decides readiness.
    report = analyze_readiness(problem)
    blockers = report.blockers
    # Curriculum coverage is checked beside the problem, not inside it: the GA
    # never reads a curriculum, so it stays out of the problem's contract. A
    # curriculum gap does not stop the GA either, so it is a warning, and only
    # blockers decide `ready`.
    warnings = analyze_curriculum_coverage(load_cohort_coverage(db, term_id))
    return {
        "term_id": term_id,
        "ready": not blockers,
        "blockers": [_serialize(blocker) for blocker in blockers],
        "warnings": [_serialize(warning) for warning in warnings],
    }


def _serialize(finding: ReadinessBlocker) -> dict:
    return {
        "code": finding.code,
        "message": finding.message,
        "details": finding.details,
    }
