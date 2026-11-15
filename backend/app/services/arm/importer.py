"""CHR-100 / CHR-COLAB-004 transactional offline RuleSet import."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.extracted_rule import ExtractedRule
from app.models.faculty import Faculty
from app.models.room import Room
from app.models.rule_set import RuleSet, RuleSetThreshold
from app.models.subject import Subject
from app.models.term import Term
from app.services.arm.rule_package import CandidateRulePackage


class RulePackageImportError(ValueError):
    def __init__(self, violations: list[str]):
        self.violations = tuple(violations)
        super().__init__("; ".join(violations))


@dataclass(frozen=True, slots=True)
class RulePackageImportSummary:
    rule_set_id: UUID
    version: int
    version_label: str | None
    rule_count: int
    training_term_count: int


def _validate_references(
    session: Session, package: CandidateRulePackage
) -> dict[tuple[str, int], Term]:
    faculty_ids = {
        UUID(rule.item_values["faculty_id"])
        for rule in package.rules
    }
    subject_codes = {
        item.value
        for rule in package.rules
        for item in (*rule.antecedent, *rule.consequent)
        if item.kind == "subject_code"
    }
    room_codes = {
        item.value
        for rule in package.rules
        for item in rule.consequent
        if item.kind == "room_code"
    }
    periods = {
        (term.academic_year, term.term_number) for term in package.training_terms
    }

    known_faculty = set(
        session.scalars(
            select(Faculty.faculty_id).where(Faculty.faculty_id.in_(faculty_ids))
        ).all()
    )
    known_subjects = (
        set(
            session.scalars(
                select(Subject.subject_code).where(
                    Subject.subject_code.in_(subject_codes)
                )
            ).all()
        )
        if subject_codes
        else set()
    )
    known_rooms = (
        set(
            session.scalars(
                select(Room.room_code).where(Room.room_code.in_(room_codes))
            ).all()
        )
        if room_codes
        else set()
    )
    terms = session.scalars(
        select(Term).where(
            tuple_(Term.academic_year, Term.term_number).in_(periods)
        )
    ).all()
    terms_by_period = {
        (term.academic_year, term.term_number): term for term in terms
    }

    violations = []
    if missing := sorted(str(value) for value in faculty_ids - known_faculty):
        violations.append(f"unknown faculty_id values: {missing}")
    if missing := sorted(subject_codes - known_subjects):
        violations.append(f"unknown subject_code values: {missing}")
    if missing := sorted(room_codes - known_rooms):
        violations.append(f"unknown room_code values: {missing}")
    if missing := sorted(periods - set(terms_by_period)):
        violations.append(
            "unknown training terms: "
            + str([f"{year} term {term}" for year, term in missing])
        )
    if violations:
        raise RulePackageImportError(violations)
    return terms_by_period


def _create_draft(
    session: Session,
    package: CandidateRulePackage,
    notes: str | None,
    version_label: str | None = None,
) -> RuleSet:
    if notes is not None and len(notes) > 500:
        raise RulePackageImportError(["notes must not exceed 500 characters"])
    if version_label is not None:
        if not version_label.strip() or len(version_label) > 20:
            raise RulePackageImportError(
                ["version label must be 1 to 20 characters"]
            )
        if session.scalar(
            select(RuleSet.rule_set_id).where(RuleSet.version_label == version_label)
        ):
            raise RulePackageImportError(
                [f"version label {version_label!r} is already used"]
            )

    last_collision: IntegrityError | None = None
    for _attempt in range(3):
        version = (session.scalar(select(func.max(RuleSet.version))) or 0) + 1
        rule_set = RuleSet(
            rule_set_id=uuid4(),
            version=version,
            status="draft",
            generated_at=package.metadata.mined_at,
            notes=notes,
            version_label=version_label,
        )
        try:
            with session.begin_nested():
                session.add(rule_set)
                session.flush()
            return rule_set
        except IntegrityError as error:
            last_collision = error
    raise RulePackageImportError(
        ["failed to assign RuleSet version after three concurrent collisions"]
    ) from last_collision


def import_rule_package(
    session: Session,
    package: CandidateRulePackage,
    *,
    notes: str | None = None,
    version_label: str | None = None,
) -> RulePackageImportSummary:
    """Validate all references, then stage one complete draft in the transaction."""
    terms_by_period = _validate_references(session, package)
    rule_set = _create_draft(session, package, notes, version_label)

    for threshold in package.thresholds:
        session.add(
            RuleSetThreshold(
                rule_set_id=rule_set.rule_set_id,
                pattern_type=threshold.pattern_type.value,
                min_support=threshold.min_support,
                min_confidence=threshold.min_confidence,
                min_lift=threshold.min_lift,
                min_term_recurrence=threshold.min_term_recurrence,
                earliest_academic_year=threshold.earliest_academic_year,
            )
        )

    for packaged_rule in package.rules:
        values = packaged_rule.item_values
        session.add(
            ExtractedRule(
                rule_set_id=rule_set.rule_set_id,
                pattern_type=packaged_rule.pattern_type.value,
                faculty_id=UUID(values["faculty_id"]),
                subject_code=values.get("subject_code"),
                room_code=values.get("room_code"),
                meeting_days=values.get("meeting_days"),
                time_slot=values.get("time_slot"),
                support=packaged_rule.support,
                confidence=packaged_rule.confidence,
                lift=packaged_rule.lift,
                term_recurrence=packaged_rule.term_recurrence,
                guidance_score=packaged_rule.guidance_score,
            )
        )

    rule_set.included_terms.extend(
        terms_by_period[(term.academic_year, term.term_number)]
        for term in package.training_terms
    )
    session.flush()
    return RulePackageImportSummary(
        rule_set_id=rule_set.rule_set_id,
        version=rule_set.version,
        version_label=rule_set.version_label,
        rule_count=len(package.rules),
        training_term_count=len(package.training_terms),
    )
