"""Versioned, server-owned hard-constraint limits (the Hard Constraints page)."""

from dataclasses import asdict

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain import ConstraintLimits
from app.models import AppUser, HardConstraintConfiguration
from app.services.audit import record_event


class ConfigurationConflictError(Exception):
    """Another save claimed the next version first."""


def get_active_configuration(db: Session) -> HardConstraintConfiguration | None:
    return db.scalar(
        select(HardConstraintConfiguration).where(HardConstraintConfiguration.is_active)
    )


def active_constraint_limits(db: Session) -> ConstraintLimits:
    """The limits every problem built now is checked against; the code
    defaults until an administrator saves a version."""
    active = get_active_configuration(db)
    return ConstraintLimits.from_saved(active.limits if active is not None else None)


def list_versions(db: Session) -> list[tuple[HardConstraintConfiguration, str | None]]:
    """Every saved version, newest first, with the saving user's name."""
    rows = db.execute(
        select(HardConstraintConfiguration, AppUser.username)
        .outerjoin(
            AppUser, AppUser.user_id == HardConstraintConfiguration.created_by_user_id
        )
        .order_by(HardConstraintConfiguration.version.desc())
    ).all()
    return [(row[0], row[1]) for row in rows]


def save_configuration(
    db: Session, limits: ConstraintLimits, actor_id: object
) -> HardConstraintConfiguration:
    """Store ``limits`` as the next version and make it the only active one."""
    # Locking the active row serializes concurrent saves; the version unique
    # constraint catches the remaining race when no row is active yet.
    previous = db.execute(
        select(HardConstraintConfiguration.version, HardConstraintConfiguration.limits)
        .where(HardConstraintConfiguration.is_active)
        .with_for_update()
    ).one_or_none()
    next_version = (
        db.scalar(select(func.max(HardConstraintConfiguration.version))) or 0
    ) + 1
    db.execute(
        update(HardConstraintConfiguration)
        .where(HardConstraintConfiguration.is_active)
        .values(is_active=False)
    )
    saved = HardConstraintConfiguration(
        version=next_version,
        is_active=True,
        limits=asdict(limits),
        created_by_user_id=actor_id,
    )
    db.add(saved)
    try:
        db.flush()
        record_event(
            db,
            actor_id=actor_id,
            operation="hard_constraints.save",
            target_type="hard_constraint_configuration",
            target_id=saved.hard_constraint_configuration_id,
            details={
                "version": next_version,
                "before": (
                    {"version": previous.version, "limits": previous.limits}
                    if previous is not None
                    else None
                ),
                "after": {"version": next_version, "limits": saved.limits},
            },
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise ConfigurationConflictError from error
    db.refresh(saved)
    return saved
