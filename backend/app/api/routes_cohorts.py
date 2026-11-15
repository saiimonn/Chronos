from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_committee_member
from app.core.errors import APIError
from app.schemas.catalog import CohortGenerateRequest, CohortGenerationResponse
from app.services.catalog.cohort_generation import (
    CohortGenerationReport,
    generate_cohort_classes,
)
from app.services.catalog.curriculum import NoApplicableCurriculumError

# Term setup is Scheduler work: the same gate as creating Blocks, offerings,
# and Block classes by hand (CHR-RBAC-002), since this creates exactly those.
router = APIRouter(
    prefix="/terms",
    tags=["cohorts"],
    dependencies=[Depends(require_committee_member)],
)

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/{term_id}/cohorts/generate",
    response_model=CohortGenerationResponse,
    status_code=status.HTTP_200_OK,
)
def generate_cohort(
    term_id: UUID, payload: CohortGenerateRequest, db: DbSession
) -> dict:
    try:
        report = generate_cohort_classes(
            db,
            term_id,
            payload.program_code,
            payload.year_level,
            payload.section_letters,
        )
    except NoApplicableCurriculumError as error:
        raise APIError(
            409,
            "NO_APPLICABLE_CURRICULUM",
            "No curriculum on file covers this cohort's entry year.",
            {"program_code": error.program_code, "entry_year": error.entry_year},
        ) from error
    return _report_body(report)


def _report_body(report: CohortGenerationReport) -> dict:
    empty_note = (
        f"The {report.curriculum_label} curriculum requires no subjects of "
        f"year {report.year_level} in term {report.term_number}."
        if not report.required_subject_codes
        else None
    )
    blocks = [
        {
            "block_id": generated.block.block_id,
            "name": generated.block.name,
            "section_letter": generated.block.section_letter,
            "created": generated.created,
            "classes": [
                {
                    "class_id": item.link.class_id,
                    "offering_id": item.offering.offering_id,
                    "subject_code": item.offering.subject_code,
                    "group_no": item.offering.group_no,
                    "group_label": item.offering.group_label,
                    "created": item.created,
                }
                for item in generated.classes
            ],
            "note": empty_note,
        }
        for generated in report.blocks
    ]
    created_classes = sum(
        item["created"] for block in blocks for item in block["classes"]
    )
    return {
        "term_id": report.term_id,
        "program_code": report.program_code,
        "year_level": report.year_level,
        "term_number": report.term_number,
        "curriculum_id": report.curriculum_id,
        "curriculum_label": report.curriculum_label,
        "required_subject_codes": report.required_subject_codes,
        "blocks": blocks,
        "blocks_created": sum(block["created"] for block in blocks),
        # Paired Blocks' new LEC classes share one new offering.
        "offerings_created": sum(
            item.offering_created
            for generated in report.blocks
            for item in generated.classes
        ),
        "classes_created": created_classes,
    }
