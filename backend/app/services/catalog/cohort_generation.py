"""Creating a cohort's Blocks and the classes its curriculum requires this term.

Split like curriculum resolution. `plan_cohort_classes` is pure: given what a
cohort already takes and what its curriculum requires, it answers what must be
created, so the rules are asserted against fixed inputs. The session-bound
`generate_cohort_classes` does the fetching, then applies the plan.

Generation only ever adds. A section letter left out of a request is not a
request to dissolve that Block, and a class the curriculum does not list is not
removed: electives and deliberate extras are added by hand and stay that way.
The one exception is a paired LEC held on two groups: the later letter moves
onto the earlier letter's group, as `merge_paired_lectures` does on any edit.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import SubjectType, partner_section_letter
from app.models import (
    BlockSection,
    BlockSectionOffering,
    CourseProgram,
    Subject,
    SubjectOffering,
)
from app.schemas.block import BlockCreate
from app.services.catalog.blocks import (
    add_block,
    link_class,
    merge_paired_lectures,
)
from app.services.catalog.curriculum import (
    required_subject_codes,
    resolve_for_cohort,
)
from app.services.catalog.guards import (
    count_where,
    ensure_reference_exists,
    require_active_term,
)
from app.services.catalog.offerings import add_next_group

# One lock key per cohort. Reading what a cohort already has and writing what it
# lacks is only idempotent if no other request for the same cohort can plan in
# between, so the whole read/plan/write section runs under this lock. A request
# for a different cohort never waits on it. Transaction-scoped, like the Group
# No. lock, so COMMIT or ROLLBACK always releases it.
_LOCK_COHORT = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))")


@dataclass(frozen=True)
class PlannedClass:
    section_letter: str
    subject_code: str


@dataclass(frozen=True)
class CohortPlan:
    blocks_to_create: tuple[str, ...]
    existing_blocks: tuple[str, ...]
    # A LEC entry joins its paired Block's offering (A-B, C-D, ...) when that
    # Block already has one; every other entry gets its own new offering.
    classes_to_create: tuple[PlannedClass, ...]
    existing_classes: tuple[PlannedClass, ...]


def plan_cohort_classes(
    section_letters: Sequence[str],
    existing_classes: Mapping[str, frozenset[str]],
    requirements: Sequence[str],
) -> CohortPlan:
    """What must be created for one cohort, and what is already in place.

    `existing_classes` maps each section letter that already has a Block to the
    subject codes that Block takes, through any offering. A letter missing from
    it has no Block yet. A requirement the Block already takes is left alone,
    whichever group supplies it.
    """

    blocks_to_create: list[str] = []
    existing_blocks: list[str] = []
    classes_to_create: list[PlannedClass] = []
    already_taken: list[PlannedClass] = []
    for letter in section_letters:
        taken = existing_classes.get(letter)
        if taken is None:
            blocks_to_create.append(letter)
            taken = frozenset()
        else:
            existing_blocks.append(letter)
        for subject_code in requirements:
            planned = PlannedClass(letter, subject_code)
            if subject_code in taken:
                already_taken.append(planned)
            else:
                classes_to_create.append(planned)
    return CohortPlan(
        blocks_to_create=tuple(blocks_to_create),
        existing_blocks=tuple(existing_blocks),
        classes_to_create=tuple(classes_to_create),
        existing_classes=tuple(already_taken),
    )


@dataclass
class GeneratedClass:
    link: BlockSectionOffering
    offering: SubjectOffering
    created: bool
    # False when a new class joined its paired Block's existing LEC offering.
    offering_created: bool = False


@dataclass
class GeneratedBlock:
    block: BlockSection
    created: bool
    classes: list[GeneratedClass] = field(default_factory=list)


@dataclass
class CohortGenerationReport:
    term_id: UUID
    program_code: str
    year_level: int
    term_number: int
    curriculum_id: UUID
    curriculum_label: str
    required_subject_codes: list[str]
    blocks: list[GeneratedBlock]


def _refuse_retired_subjects(db: Session, subject_codes: Sequence[str]) -> None:
    """Refuse generating classes for a curriculum subject that was retired.

    Retired subjects stay for history but are never newly offered, so the
    curriculum must be revised or the subject restored first.
    """
    if not subject_codes:
        return
    retired = sorted(
        db.scalars(
            select(Subject.subject_code).where(
                Subject.subject_code.in_(subject_codes),
                Subject.is_active.is_(False),
            )
        ).all()
    )
    if retired:
        raise APIError(
            409,
            "INACTIVE_REFERENCE",
            "The curriculum requires subjects that are marked inactive.",
            {"field": "subject_code", "value": ", ".join(retired)},
        )


def generate_cohort_classes(
    db: Session,
    term_id: UUID,
    program_code: str,
    year_level: int,
    section_letters: Sequence[str],
) -> CohortGenerationReport:
    """Create a cohort's missing Blocks, offerings, and class links.

    Every write for the request commits together or not at all. The curriculum
    is resolved before anything is written, so `NoApplicableCurriculumError`
    leaves the term exactly as it was. Running this again creates nothing and
    reports what already existed, including when two identical requests arrive
    together: the second waits for the first to commit, then plans against it.
    """

    term = require_active_term(db, term_id)
    # Checked before resolving, so an unknown program reads as a bad reference
    # rather than as a program that merely lacks a curriculum.
    ensure_reference_exists(
        db, CourseProgram, program_code, "program_code", require_active_record=True
    )
    # Every Block of one cohort shares program, year level, and term, so they
    # all resolve to the same curriculum; `resolve_for_block` would answer the
    # same question once per Block.
    curriculum = resolve_for_cohort(
        db, program_code, year_level, term.academic_year
    )
    requirements = required_subject_codes(
        db, curriculum, year_level, term.term_number
    )
    _refuse_retired_subjects(db, requirements)

    letters = list(dict.fromkeys(letter.upper() for letter in section_letters))
    # Taken before the existing Blocks are read, so the read sees whatever a
    # concurrent request for this cohort committed while this one waited.
    # Group No. locks are taken afterwards, in subject-code order, so two
    # cohorts sharing subjects always acquire them in the same order.
    db.execute(
        _LOCK_COHORT,
        {"lock_key": f"cohort:{term_id}:{program_code}:{year_level}"},
    )
    blocks = {
        block.section_letter: block
        for block in db.scalars(
            select(BlockSection).where(
                BlockSection.term_id == term_id,
                BlockSection.program_code == program_code,
                BlockSection.year_level == year_level,
                BlockSection.section_letter.in_(letters),
            )
        ).all()
    }
    # Blocks set up before lecture pairing, or by hand, can still hold a
    # paired LEC on two groups (A on G1, B on G2). Merge those first, so the
    # plan below reads one shared offering per paired LEC.
    for block in blocks.values():
        merge_paired_lectures(db, block)
    links: dict[tuple[str, str], BlockSectionOffering] = {}
    if blocks:
        by_block_id = {block.block_id: letter for letter, block in blocks.items()}
        for link in db.scalars(
            select(BlockSectionOffering).where(
                BlockSectionOffering.block_id.in_(by_block_id)
            )
        ).all():
            links[(by_block_id[link.block_id], link.subject_code)] = link
    existing_classes = {
        letter: frozenset(
            subject for taken_letter, subject in links if taken_letter == letter
        )
        for letter in blocks
    }

    plan = plan_cohort_classes(letters, existing_classes, requirements)

    try:
        created_blocks = set(plan.blocks_to_create)
        for letter in plan.blocks_to_create:
            blocks[letter] = add_block(
                db,
                BlockCreate(
                    term_id=term_id,
                    program_code=program_code,
                    year_level=year_level,
                    section_letter=letter,
                ),
            )
        report_blocks = {
            letter: GeneratedBlock(blocks[letter], letter in created_blocks)
            for letter in letters
        }
        for planned in plan.existing_classes:
            link = links[(planned.section_letter, planned.subject_code)]
            report_blocks[planned.section_letter].classes.append(
                GeneratedClass(link, link.offering, created=False)
            )
        lectures = {
            subject.subject_code
            for subject in db.scalars(
                select(Subject).where(Subject.subject_code.in_(requirements))
            )
            if SubjectType(subject.subject_type) is SubjectType.LEC
        }
        offerings = {key: link.offering for key, link in links.items()}
        for planned in plan.classes_to_create:
            # Paired Blocks attend one session for each LEC they share.
            offering = (
                offerings.get(
                    (partner_section_letter(planned.section_letter), planned.subject_code)
                )
                if planned.subject_code in lectures
                else None
            )
            if offering is not None and db.scalar(
                count_where(
                    BlockSectionOffering,
                    BlockSectionOffering.offering_id == offering.offering_id,
                )
            ) >= SubjectType.LEC.max_blocks_per_offering:
                offering = None
            offering_created = offering is None
            if offering_created:
                offering = add_next_group(db, term_id, planned.subject_code)
                db.flush()
            link = link_class(db, blocks[planned.section_letter], offering)
            offerings[(planned.section_letter, planned.subject_code)] = offering
            report_blocks[planned.section_letter].classes.append(
                GeneratedClass(link, offering, created=True, offering_created=offering_created)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    for generated in report_blocks.values():
        generated.classes.sort(key=lambda item: item.link.subject_code)
    return CohortGenerationReport(
        term_id=term_id,
        program_code=program_code,
        year_level=year_level,
        term_number=term.term_number,
        curriculum_id=curriculum.curriculum_id,
        curriculum_label=curriculum.label,
        required_subject_codes=requirements,
        blocks=list(report_blocks.values()),
    )
