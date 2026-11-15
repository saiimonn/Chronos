"""CHR-119 / CHR-CFG-001 versioned, server-owned GA configuration."""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppUser, GAConfiguration
from app.services.audit import record_event
from app.services.ga.config import GAConfig, validate_meeting_day_patterns
from app.services.jobs.codecs import decode_ga_config, encode_ga_config


class ConfigurationConflictError(Exception):
    """Another save claimed the next version first."""


def get_active_configuration(db: Session) -> GAConfiguration | None:
    return db.scalar(select(GAConfiguration).where(GAConfiguration.is_active))


def list_versions(db: Session) -> list[tuple[GAConfiguration, str | None]]:
    """Every saved version, newest first, with the saving user's name."""
    rows = db.execute(
        select(GAConfiguration, AppUser.username)
        .outerjoin(AppUser, AppUser.user_id == GAConfiguration.created_by_user_id)
        .order_by(GAConfiguration.version.desc())
    ).all()
    return [(row[0], row[1]) for row in rows]


def active_ga_config(db: Session) -> GAConfig:
    """The configuration a server-configured GA run uses.

    Falls back to the code defaults only when no version exists, which the
    migration's seeded version 1 rules out in a migrated database.
    """
    active = get_active_configuration(db)
    return decode_ga_config(active.parameters) if active is not None else GAConfig()


def save_configuration(
    db: Session, config: GAConfig, actor_id: object
) -> GAConfiguration:
    """Store ``config`` as the next version and make it the only active one."""
    validate_meeting_day_patterns(config.meeting_day_patterns)
    # Locking the active row serializes concurrent saves; the version unique
    # constraint catches the remaining race when no row is active yet.
    previous = db.execute(
        select(GAConfiguration.version, GAConfiguration.parameters)
        .where(GAConfiguration.is_active)
        .with_for_update()
    ).one_or_none()
    next_version = (db.scalar(select(func.max(GAConfiguration.version))) or 0) + 1
    db.execute(
        update(GAConfiguration)
        .where(GAConfiguration.is_active)
        .values(is_active=False)
    )
    saved = GAConfiguration(
        version=next_version,
        is_active=True,
        parameters=encode_ga_config(config),
        created_by_user_id=actor_id,
    )
    db.add(saved)
    try:
        db.flush()
        record_event(
            db,
            actor_id=actor_id,
            operation="ga_configuration.save",
            target_type="ga_configuration",
            target_id=saved.ga_configuration_id,
            details={
                "version": next_version,
                "before": (
                    {"version": previous.version, "parameters": previous.parameters}
                    if previous is not None
                    else None
                ),
                "after": {"version": next_version, "parameters": saved.parameters},
            },
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise ConfigurationConflictError from error
    db.refresh(saved)
    return saved
