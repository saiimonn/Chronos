from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import BlockSectionOffering, ScheduleRecord, Subject, SubjectOffering
from app.schemas.offering import OfferingCreate
from app.services.catalog.guards import (
    count_where,
    delete_guarded,
    ensure_reference_exists,
    require_active_term,
)

# One lock key per (term, subject) so two people adding groups to different
# subjects never wait on each other. The lock is transaction-scoped: PostgreSQL
# releases it at COMMIT or ROLLBACK, so no path can leak it.
_LOCK_GROUP_NO_SEQUENCE = text(
    "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"
)


def list_offerings(db: Session, term_id: UUID) -> list[SubjectOffering]:
    return list(
        db.scalars(
            select(SubjectOffering)
            .where(SubjectOffering.term_id == term_id)
            .order_by(SubjectOffering.subject_code, SubjectOffering.group_no)
        ).all()
    )


def get_offering(db: Session, offering_id: UUID) -> SubjectOffering:
    offering = db.get(SubjectOffering, offering_id)
    if offering is None:
        raise APIError(
            404, "NOT_FOUND", "The requested subject offering was not found."
        )
    return offering


def add_next_group(
    db: Session, term_id: UUID, subject_code: str
) -> SubjectOffering:
    """Add an offering at the next Group No., without committing.

    Reading the highest number and inserting one above it is only correct if no
    other transaction can do the same thing in between, so the pair runs under
    an advisory lock. The unique constraint remains the last word: it is what
    makes a missed lock a failed write rather than a duplicate G1.

    Term selection is deliberately not checked here. This is the numbering
    itself, shared by the API — which does check — and by the Block migration
    preflight, which fills gaps in terms that are long closed.
    """
    db.execute(
        _LOCK_GROUP_NO_SEQUENCE,
        {"lock_key": f"{term_id}:{subject_code}"},
    )
    next_group_no = db.scalar(
        select(func.coalesce(func.max(SubjectOffering.group_no), 0) + 1).where(
            SubjectOffering.term_id == term_id,
            SubjectOffering.subject_code == subject_code,
        )
    )
    offering = SubjectOffering(
        term_id=term_id,
        subject_code=subject_code,
        group_no=next_group_no,
    )
    db.add(offering)
    return offering


def compact_group_numbers(db: Session, term_id: UUID, subject_code: str) -> bool:
    """Renumber a subject's groups in a term to 1..n, keeping their order.

    Used after a lecture merge removes a group, so paired Blocks read G1, G2,
    G3 rather than G1, G3, G5. Without committing.

    Left alone, and False returned, once any group of the subject has a
    scheduled class: a published label like "CIS 1203 G3" must not start
    naming a different session. Takes the same lock as `add_next_group`, so a
    concurrent add cannot claim a number mid-renumber.
    """
    db.execute(
        _LOCK_GROUP_NO_SEQUENCE,
        {"lock_key": f"{term_id}:{subject_code}"},
    )
    offerings = db.scalars(
        select(SubjectOffering)
        .where(
            SubjectOffering.term_id == term_id,
            SubjectOffering.subject_code == subject_code,
        )
        .order_by(SubjectOffering.group_no)
    ).all()
    if db.scalar(
        count_where(
            ScheduleRecord,
            ScheduleRecord.class_id.in_(
                select(BlockSectionOffering.class_id).where(
                    BlockSectionOffering.offering_id.in_(
                        [offering.offering_id for offering in offerings]
                    )
                )
            ),
        )
    ):
        return False
    # Ascending, one flush at a time: each target number is at most the row's
    # own and already vacated, so the unique constraint never sees a duplicate.
    for group_no, offering in enumerate(offerings, start=1):
        if offering.group_no != group_no:
            offering.group_no = group_no
            db.flush()
    return True


def create_offering(db: Session, payload: OfferingCreate) -> SubjectOffering:
    """Add an offering to the active term, at the next Group No."""
    require_active_term(db, payload.term_id)
    ensure_reference_exists(
        db, Subject, payload.subject_code, "subject_code", require_active_record=True
    )
    offering = add_next_group(db, payload.term_id, payload.subject_code)
    db.commit()
    db.refresh(offering)
    return offering


def delete_offering(db: Session, offering_id: UUID) -> None:
    """Remove one offering, leaving every surviving Group No. as it was.

    Renumbering would silently rename groups people have already been told to
    attend, so a deletion simply leaves a gap.
    """
    offering = get_offering(db, offering_id)
    require_active_term(db, offering.term_id)
    # A Block class takes this offering (ON DELETE RESTRICT); its schedule
    # records hang off that class, so naming the class covers them.
    delete_guarded(
        db,
        offering,
        [
            (
                "block_classes",
                count_where(
                    BlockSectionOffering,
                    BlockSectionOffering.offering_id == offering_id,
                ),
            )
        ],
    )
