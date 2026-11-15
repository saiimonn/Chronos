from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import SubjectType, partner_section_letter
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    ScheduleRecord,
    Subject,
    SubjectOffering,
)
from app.schemas.block import BlockClassCreate, BlockCreate, BlockUpdate
from app.services.catalog.guards import (
    count_where,
    delete_guarded,
    ensure_reference_exists,
    ensure_unique,
    require_active_term,
)
from app.services.catalog.offerings import compact_group_numbers


def list_blocks(db: Session, term_id: UUID) -> list[BlockSection]:
    return list(
        db.scalars(
            select(BlockSection)
            .where(BlockSection.term_id == term_id)
            .order_by(
                BlockSection.program_code,
                BlockSection.year_level,
                BlockSection.section_letter,
                BlockSection.block_id,
            )
        ).all()
    )


def get_block(db: Session, block_id: UUID) -> BlockSection:
    block = db.get(BlockSection, block_id)
    if block is None:
        raise APIError(404, "NOT_FOUND", "The requested Block Section was not found.")
    return block


def add_block(db: Session, payload: BlockCreate) -> BlockSection:
    """Add a Block under the creation rules, without committing.

    Term selection is left to the caller, as in `offerings.add_next_group`, so
    cohort generation can check it once and apply many Blocks in a single
    transaction.
    """
    ensure_reference_exists(
        db,
        CourseProgram,
        payload.program_code,
        "program_code",
        require_active_record=True,
    )
    section_letter = payload.section_letter.upper()
    ensure_unique(
        db,
        BlockSection,
        term_id=payload.term_id,
        program_code=payload.program_code,
        year_level=payload.year_level,
        section_letter=section_letter,
    )
    block = BlockSection(
        **(payload.model_dump() | {"section_letter": section_letter})
    )
    db.add(block)
    db.flush()
    return block


def create_block(db: Session, payload: BlockCreate) -> BlockSection:
    require_active_term(db, payload.term_id)
    block = add_block(db, payload)
    merge_paired_lectures(db, block)
    db.commit()
    db.refresh(block)
    return block


def update_block(db: Session, block_id: UUID, payload: BlockUpdate) -> BlockSection:
    block = get_block(db, block_id)
    require_active_term(db, block.term_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(block, field, value)
    db.commit()
    db.refresh(block)
    return block


def delete_block(db: Session, block_id: UUID) -> None:
    block = get_block(db, block_id)
    require_active_term(db, block.term_id)
    delete_guarded(
        db,
        block,
        [
            (
                "schedule_records",
                count_where(
                    ScheduleRecord,
                    ScheduleRecord.class_id.in_(
                        select(BlockSectionOffering.class_id).where(
                            BlockSectionOffering.block_id == block_id
                        )
                    ),
                ),
            )
        ],
    )


def list_classes(db: Session, block_id: UUID) -> list[BlockSectionOffering]:
    get_block(db, block_id)
    return list(
        db.scalars(
            select(BlockSectionOffering)
            .where(BlockSectionOffering.block_id == block_id)
            .order_by(
                BlockSectionOffering.subject_code, BlockSectionOffering.class_id
            )
        ).all()
    )


def add_class(
    db: Session, block_id: UUID, payload: BlockClassCreate
) -> BlockSectionOffering:
    """Link a Block to one Subject Offering.

    The database keeps a Block from taking two groups of one subject, but not
    from overfilling an offering: how many Blocks an offering may serve depends
    on the subject's LAB/LEC type, which sits two joins from the association
    row. That rule is enforced in `link_class`.
    """
    block = get_block(db, block_id)
    require_active_term(db, block.term_id)

    offering = db.get(SubjectOffering, payload.offering_id)
    if offering is None:
        raise APIError(
            409,
            "REFERENCE_NOT_FOUND",
            "The requested subject offering was not found.",
            {"field": "offering_id"},
        )
    link = link_class(db, block, offering)
    merge_paired_lectures(db, block)
    db.commit()
    # The merge may have replaced this very link with one on the partner's offering.
    link = db.scalar(
        select(BlockSectionOffering).where(
            BlockSectionOffering.block_id == block.block_id,
            BlockSectionOffering.subject_code == offering.subject_code,
        )
    )
    db.refresh(link)
    return link


def link_class(
    db: Session, block: BlockSection, offering: SubjectOffering
) -> BlockSectionOffering:
    """Link a Block to an offering under the class rules, without committing.

    Term selection is left to the caller, as in `add_block`.
    """
    if offering.term_id != block.term_id:
        raise APIError(
            409,
            "TERM_MISMATCH",
            "A Block may only take offerings from its own term.",
            {"block_term_id": str(block.term_id), "offering_term_id": str(offering.term_id)},
        )
    ensure_unique(
        db,
        BlockSectionOffering,
        block_id=block.block_id,
        term_id=block.term_id,
        subject_code=offering.subject_code,
    )

    subject = db.get(Subject, offering.subject_code)
    limit = SubjectType(subject.subject_type).max_blocks_per_offering
    linked = db.scalar(
        select(func.count())
        .select_from(BlockSectionOffering)
        .where(BlockSectionOffering.offering_id == offering.offering_id)
    )
    if linked >= limit:
        raise APIError(
            409,
            "OFFERING_BLOCK_LIMIT_REACHED",
            f"A {subject.subject_type} offering may serve at most {limit} "
            "Block Section(s).",
            {
                "offering_id": str(offering.offering_id),
                "linked_blocks": linked,
                "limit": limit,
            },
        )

    link = BlockSectionOffering(
        block_id=block.block_id,
        offering_id=offering.offering_id,
        term_id=block.term_id,
        subject_code=offering.subject_code,
    )
    db.add(link)
    db.flush()
    return link


def remove_class(db: Session, block_id: UUID, class_id: UUID) -> None:
    link = db.get(BlockSectionOffering, class_id)
    if link is None or link.block_id != block_id:
        raise APIError(404, "NOT_FOUND", "The requested Block class was not found.")
    require_active_term(db, link.term_id)
    delete_guarded(
        db,
        link,
        [
            (
                "schedule_records",
                count_where(ScheduleRecord, ScheduleRecord.class_id == class_id),
            )
        ],
    )



def find_partner(db: Session, block: BlockSection) -> BlockSection | None:
    """The Block paired with this one by section letter (A-B, C-D, ...)."""
    letter = partner_section_letter(block.section_letter)
    if letter is None:
        return None
    return db.scalar(
        select(BlockSection).where(
            BlockSection.term_id == block.term_id,
            BlockSection.program_code == block.program_code,
            BlockSection.year_level == block.year_level,
            BlockSection.section_letter == letter,
        )
    )


def merge_paired_lectures(db: Session, block: BlockSection) -> None:
    """Put a Block and its partner on one offering for every LEC both take.

    The later letter moves onto the earlier letter's offering, and an offering
    left serving nobody is removed; that subject's remaining groups are then
    renumbered 1..n (`compact_group_numbers`). A class that already has schedule
    records, or a target offering already serving two Blocks, stays as it is:
    moving it would rewrite a published session. Does not commit.
    """
    partner = find_partner(db, block)
    if partner is None:
        return
    first, second = sorted((block, partner), key=lambda item: item.section_letter)
    first_offerings = {
        link.subject_code: link.offering_id
        for link in db.scalars(
            select(BlockSectionOffering).where(
                BlockSectionOffering.block_id == first.block_id
            )
        )
    }
    second_links = db.scalars(
        select(BlockSectionOffering).where(
            BlockSectionOffering.block_id == second.block_id
        )
    ).all()
    emptied: set[str] = set()
    for link in second_links:
        target_id = first_offerings.get(link.subject_code)
        if target_id in (None, link.offering_id):
            continue
        subject = db.get(Subject, link.subject_code)
        if SubjectType(subject.subject_type) is not SubjectType.LEC:
            continue
        target = db.get(SubjectOffering, target_id)
        if db.scalar(
            count_where(ScheduleRecord, ScheduleRecord.class_id == link.class_id)
        ) or db.scalar(
            count_where(BlockSectionOffering, BlockSectionOffering.offering_id == target_id)
        ) >= SubjectType.LEC.max_blocks_per_offering:
            continue
        old_offering_id = link.offering_id
        db.delete(link)
        db.flush()
        link_class(db, second, target)
        if not db.scalar(
            count_where(
                BlockSectionOffering,
                BlockSectionOffering.offering_id == old_offering_id,
            )
        ):
            db.delete(db.get(SubjectOffering, old_offering_id))
            db.flush()
            emptied.add(link.subject_code)
    # Close the gap the removed group left (G1, G3 -> G1, G2). In subject
    # order, like every other Group No. lock, so two writers cannot deadlock.
    for subject_code in sorted(emptied):
        compact_group_numbers(db, block.term_id, subject_code)
