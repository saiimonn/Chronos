from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain import encode_preferred_days
from app.models import Faculty, Subject
from app.schemas.faculty import FacultyCreate, FacultyUpdate


def list_faculty(db: Session, include_inactive: bool = False) -> list[Faculty]:
    query = select(Faculty).order_by(Faculty.full_name)
    if not include_inactive:
        query = query.where(Faculty.is_active)
    return list(db.scalars(query).all())


def get_faculty(db: Session, faculty_id: UUID) -> Faculty:
    faculty = db.get(Faculty, faculty_id)
    if faculty is None:
        raise APIError(404, "NOT_FOUND", "The requested faculty member was not found.")
    return faculty


def _require_disjoint(faculty: Faculty) -> None:
    """A subject cannot be both preferred and non-preferred."""
    both = sorted(
        set(faculty.preferred_subject_codes) & set(faculty.non_preferred_subject_codes)
    )
    if both:
        raise APIError(
            422,
            "SUBJECT_PREFERENCE_CONFLICT",
            "A subject cannot be both preferred and non-preferred.",
            {"subject_codes": both},
        )


def _resolve_subjects(db: Session, codes: list[str]) -> list[Subject]:
    """Load the named subjects, refusing any code the catalog does not have."""
    if not codes:
        return []
    subjects = list(
        db.scalars(
            select(Subject)
            .where(Subject.subject_code.in_(codes))
            .order_by(Subject.subject_code)
        ).all()
    )
    unknown = sorted(set(codes) - {subject.subject_code for subject in subjects})
    if unknown:
        raise APIError(
            422,
            "UNKNOWN_SUBJECT",
            "Preferred and non-preferred subjects must be catalog subjects.",
            {"subject_codes": unknown},
        )
    return subjects


def create_faculty(db: Session, payload: FacultyCreate) -> Faculty:
    # An availability mask only means something against a term's slot grid, so a
    # new record starts unset, which every reader treats as open in every slot
    # until an operator saves a narrower selection.
    faculty = Faculty(
        full_name=payload.full_name,
        is_full_time=payload.is_full_time,
        is_active=payload.is_active,
        preferred_days=encode_preferred_days(payload.preferred_days),
        availability_mask="",
        lecture_only=payload.lecture_only,
        preferred_subjects=_resolve_subjects(db, payload.preferred_subject_codes),
        non_preferred_subjects=_resolve_subjects(
            db, payload.non_preferred_subject_codes
        ),
    )
    _require_disjoint(faculty)
    db.add(faculty)
    db.commit()
    db.refresh(faculty)
    return faculty


def update_faculty(db: Session, faculty_id: UUID, payload: FacultyUpdate) -> Faculty:
    faculty = get_faculty(db, faculty_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field == "preferred_days":
            value = encode_preferred_days(value)
        elif field == "preferred_subject_codes":
            faculty.preferred_subjects = _resolve_subjects(db, value)
            continue
        elif field == "non_preferred_subject_codes":
            faculty.non_preferred_subjects = _resolve_subjects(db, value)
            continue
        setattr(faculty, field, value)
    _require_disjoint(faculty)
    db.commit()
    db.refresh(faculty)
    return faculty


def set_faculty_status(
    db: Session, faculty_ids: list[UUID], is_active: bool
) -> list[Faculty]:
    """Mark every listed member active or inactive, or change nothing.

    One unknown id refuses the whole request, so the roster never ends up
    half-applied. Members already in the requested state are left as is.
    """
    wanted = set(faculty_ids)
    members = list(
        db.scalars(
            select(Faculty)
            .where(Faculty.faculty_id.in_(wanted))
            .order_by(Faculty.full_name)
        ).all()
    )
    missing = wanted - {member.faculty_id for member in members}
    if missing:
        raise APIError(
            404,
            "NOT_FOUND",
            "Some of the selected faculty members were not found.",
            {"faculty_ids": sorted(str(faculty_id) for faculty_id in missing)},
        )
    for member in members:
        member.is_active = is_active
    db.commit()
    for member in members:
        db.refresh(member)
    return members


def deactivate_faculty(db: Session, faculty_id: UUID) -> Faculty:
    """Retire a faculty member without removing the row.

    Faculty are never hard-deleted: past schedules, captured history and
    extracted rules all name them, and a missing row would silently break
    those references. Deactivating keeps every record intact and takes the
    member out of scheduling; reactivating is a PATCH of ``is_active``.
    """
    faculty = get_faculty(db, faculty_id)
    if faculty.is_active:
        faculty.is_active = False
        db.commit()
        db.refresh(faculty)
    return faculty
