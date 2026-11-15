from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import CourseProgram
from app.schemas.catalog import ProgramCreate, ProgramUpdate
from app.services.catalog.guards import ensure_unique


def list_programs(db: Session, include_inactive: bool = False) -> list[CourseProgram]:
    query = select(CourseProgram).order_by(CourseProgram.program_code)
    if not include_inactive:
        query = query.where(CourseProgram.is_active)
    return list(db.scalars(query).all())


def get_program(db: Session, program_code: str) -> CourseProgram:
    program = db.get(CourseProgram, program_code)
    if program is None:
        raise APIError(404, "NOT_FOUND", "The requested course program was not found.")
    return program


def create_program(db: Session, payload: ProgramCreate) -> CourseProgram:
    ensure_unique(db, CourseProgram, program_code=payload.program_code)
    program = CourseProgram(**payload.model_dump())
    db.add(program)
    db.commit()
    db.refresh(program)
    return program


def update_program(
    db: Session, program_code: str, payload: ProgramUpdate
) -> CourseProgram:
    """Edit a program, including retiring (is_active=False) or restoring it.

    A program is never deleted: past Blocks and curricula name it. Retiring
    one only stops new Blocks being created for it; readiness reports any
    current-term Block that still belongs to it.
    """
    program = get_program(db, program_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(program, field, value)
    db.commit()
    db.refresh(program)
    return program


def deactivate_program(db: Session, program_code: str) -> CourseProgram:
    """Retire a program without removing the row (what DELETE does)."""
    return update_program(db, program_code, ProgramUpdate(is_active=False))
