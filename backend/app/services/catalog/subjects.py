from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import APIError
from app.domain.meeting_patterns import encode_preferred_patterns
from app.models import CurriculumRequirement, Room, Subject
from app.schemas.catalog import SubjectCreate, SubjectUpdate
from app.services.catalog.guards import ensure_unique


def list_subjects(db: Session, include_inactive: bool = False) -> list[Subject]:
    query = (
        select(Subject)
        .options(
            selectinload(Subject.curriculum_requirements).selectinload(
                CurriculumRequirement.curriculum
            )
        )
        .order_by(Subject.subject_code)
    )
    if not include_inactive:
        query = query.where(Subject.is_active)
    return list(db.scalars(query).all())


def get_subject(db: Session, subject_code: str) -> Subject:
    subject = db.get(Subject, subject_code)
    if subject is None:
        raise APIError(404, "NOT_FOUND", "The requested subject was not found.")
    return subject


def _resolve_rooms(
    db: Session, codes: list[str], kept: frozenset[str] = frozenset()
) -> list[Room]:
    """Load the named rooms, refusing unknown codes and newly chosen retired rooms.

    `kept` is the subject's current allowed rooms: one retired since it was
    chosen may stay, so saving other fields does not fail.
    """
    if not codes:
        return []
    rooms = list(
        db.scalars(
            select(Room).where(Room.room_code.in_(codes)).order_by(Room.room_code)
        ).all()
    )
    unknown = sorted(set(codes) - {room.room_code for room in rooms})
    if unknown:
        raise APIError(
            422,
            "UNKNOWN_ROOM",
            "Allowed rooms must be catalog rooms.",
            {"room_codes": unknown},
        )
    retired = sorted(
        room.room_code
        for room in rooms
        if not room.is_active and room.room_code not in kept
    )
    if retired:
        raise APIError(
            422,
            "INACTIVE_ROOM",
            "A retired room cannot be chosen as an allowed room.",
            {"room_codes": retired},
        )
    return rooms


def create_subject(db: Session, payload: SubjectCreate) -> Subject:
    ensure_unique(db, Subject, subject_code=payload.subject_code)
    values = payload.model_dump()
    values["preferred_day_patterns"] = encode_preferred_patterns(
        values["preferred_day_patterns"]
    )
    values["allowed_rooms"] = _resolve_rooms(db, values.pop("allowed_room_codes"))
    subject = Subject(**values)
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


def update_subject(db: Session, subject_code: str, payload: SubjectUpdate) -> Subject:
    """Edit a subject, including retiring (is_active=False) or restoring it.

    A subject is never deleted: past schedules, snapshots and mined rules name
    it. Retiring one only keeps it out of new offerings; readiness reports any
    current-term class that still takes it.
    """
    subject = get_subject(db, subject_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            if field == "preferred_day_patterns":
                value = encode_preferred_patterns(value)
            elif field == "allowed_room_codes":
                kept = frozenset(room.room_code for room in subject.allowed_rooms)
                field, value = "allowed_rooms", _resolve_rooms(db, value, kept)
            setattr(subject, field, value)
    db.commit()
    db.refresh(subject)
    return subject


def deactivate_subject(db: Session, subject_code: str) -> Subject:
    """Retire a subject without removing the row (what DELETE does)."""
    return update_subject(db, subject_code, SubjectUpdate(is_active=False))
