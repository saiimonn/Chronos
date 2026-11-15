"""Offline faculty-metric packages: attach per-teacher diagnostics to a version.

An offline candidate package carries only the rules that passed, so a version
imported from one has no record of the teachers left out. The Colab script
`build_faculty_metrics.py` recomputes every teacher's closest candidate from
the same frozen transactions with `best_faculty_patterns`, and this module
attaches that file to the already-imported version.

Attaching adds diagnostic rows only. The version's rules, thresholds and terms
are not touched, and the file is refused unless it provably belongs to the
version: its thresholds must equal the version's, and every teacher the version
holds a rule for must appear with a pattern that met all four thresholds.
"""

from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.extracted_rule import ExtractedRule
from app.models.faculty import Faculty
from app.models.rule_set import RuleSet, RuleSetThreshold
from app.models.rule_set_faculty_metric import RuleSetFacultyMetric

PatternTypeName = Literal[
    "teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room"
]


class FacultyMetricsPackageError(ValueError):
    """The package cannot be attached to this version."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageThreshold(_Strict):
    min_support: Decimal
    min_confidence: Decimal
    min_lift: Decimal
    min_term_recurrence: int


class PackageStream(_Strict):
    training_term_count: int = Field(ge=1)
    transaction_count: int = Field(ge=1)


class PackageMetric(_Strict):
    stream: PatternTypeName
    source_faculty_key: str = Field(min_length=1, max_length=64)
    faculty_name: str = Field(min_length=1, max_length=150)
    faculty_id: UUID | None
    subject_code: str | None
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
    offering_count: int = Field(ge=1)
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int = Field(ge=1)
    meets_support: bool
    meets_confidence: bool
    meets_lift: bool
    meets_term_recurrence: bool

    @property
    def meets_all(self) -> bool:
        return (
            self.meets_support
            and self.meets_confidence
            and self.meets_lift
            and self.meets_term_recurrence
        )


class FacultyMetricsPackage(_Strict):
    schema_version: Literal[1]
    source_run: str
    input_sha256: dict[str, str]
    thresholds: dict[PatternTypeName, PackageThreshold]
    streams: dict[PatternTypeName, PackageStream]
    metrics: list[PackageMetric]


def load_faculty_metrics_package(path: Path) -> FacultyMetricsPackage:
    return FacultyMetricsPackage.model_validate_json(path.read_text(encoding="utf-8"))


def attach_faculty_metrics(
    session: Session, rule_set_id: UUID, package: FacultyMetricsPackage
) -> int:
    """Insert the package's rows for ``rule_set_id``; return how many."""
    if session.get(RuleSet, rule_set_id) is None:
        raise FacultyMetricsPackageError(f"rule set {rule_set_id} does not exist")
    existing = session.scalar(
        select(func.count())
        .select_from(RuleSetFacultyMetric)
        .where(RuleSetFacultyMetric.rule_set_id == rule_set_id)
    )
    if existing:
        raise FacultyMetricsPackageError(
            f"rule set {rule_set_id} already has {existing} faculty metric rows"
        )

    recorded = {
        threshold.pattern_type: threshold
        for threshold in session.scalars(
            select(RuleSetThreshold).where(RuleSetThreshold.rule_set_id == rule_set_id)
        )
    }
    if set(recorded) != set(package.thresholds):
        raise FacultyMetricsPackageError(
            "the package covers different streams than the rule set"
        )
    for stream, threshold in package.thresholds.items():
        version = recorded[stream]
        if (
            version.min_support != threshold.min_support
            or version.min_confidence != threshold.min_confidence
            or version.min_lift != threshold.min_lift
            or version.min_term_recurrence != threshold.min_term_recurrence
        ):
            raise FacultyMetricsPackageError(
                f"{stream} thresholds differ from the rule set's"
            )

    roster = set(session.scalars(select(Faculty.faculty_id)))
    unknown = sorted(
        {str(m.faculty_id) for m in package.metrics if m.faculty_id is not None}
        - {str(faculty_id) for faculty_id in roster}
    )
    if unknown:
        raise FacultyMetricsPackageError(
            f"faculty IDs not on the roster: {', '.join(unknown)}"
        )

    persisted = set(
        session.execute(
            select(ExtractedRule.pattern_type, ExtractedRule.faculty_id)
            .where(ExtractedRule.rule_set_id == rule_set_id)
            .distinct()
        ).tuples()
    )
    passing = {
        (metric.stream, metric.faculty_id)
        for metric in package.metrics
        if metric.faculty_id is not None and metric.meets_all
    }
    missing = persisted - passing
    if missing:
        raise FacultyMetricsPackageError(
            f"{len(missing)} teacher/stream pairs with rules in this version have no "
            "passing pattern in the package; it was not built from this version's run"
        )

    rows = [
        RuleSetFacultyMetric(
            rule_set_id=rule_set_id,
            pattern_type=metric.stream,
            source_faculty_key=metric.source_faculty_key,
            faculty_id=metric.faculty_id,
            faculty_name=metric.faculty_name,
            subject_code=metric.subject_code,
            room_code=metric.room_code,
            meeting_days=metric.meeting_days,
            time_slot=metric.time_slot,
            offering_count=metric.offering_count,
            support=metric.support,
            confidence=metric.confidence,
            lift=metric.lift,
            term_recurrence=metric.term_recurrence,
            meets_support=metric.meets_support,
            meets_confidence=metric.meets_confidence,
            meets_lift=metric.meets_lift,
            meets_term_recurrence=metric.meets_term_recurrence,
            has_rule=(metric.stream, metric.faculty_id) in persisted,
        )
        for metric in package.metrics
    ]
    session.add_all(rows)
    session.flush()
    return len(rows)
