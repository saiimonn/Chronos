"""CHR-100 / CHR-COLAB-004 portable candidate RuleSet contract."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.arm.mining import guidance_score
from app.services.arm.preprocessing import HistoricalInclusionPolicy, PatternStream


_ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{4})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

RuleItemKind = Literal[
    "faculty_id",
    "subject_code",
    "room_code",
    "meeting_days",
    "time_slot",
]

_STREAM_ITEMS: dict[PatternStream, tuple[tuple[str, ...], tuple[str, ...]]] = {
    PatternStream.TEACHER_SUBJECT: (("faculty_id",), ("subject_code",)),
    PatternStream.TEACHER_DAY: (("faculty_id",), ("meeting_days",)),
    PatternStream.TEACHER_TIME: (("faculty_id",), ("time_slot",)),
    PatternStream.TEACHER_SUBJECT_ROOM: (
        ("faculty_id", "subject_code"),
        ("room_code",),
    ),
}


class _PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RulePackageMetadata(_PackageModel):
    mined_at: AwareDatetime
    packaged_at: AwareDatetime
    source_manifest_sha256: str
    evaluation_manifest_sha256: str
    selected_rules_sha256: str
    faculty_crosswalk_sha256: str
    # CHR-133 / CHR-COLAB-007: optional reviewed subject/room aliases and the
    # per-stream count of frozen rules they excluded.
    reference_aliases_sha256: str | None = None
    excluded_rule_counts: dict[PatternStream, int] | None = None

    @field_validator(
        "source_manifest_sha256",
        "evaluation_manifest_sha256",
        "selected_rules_sha256",
        "faculty_crosswalk_sha256",
        "reference_aliases_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("artifact hashes must be lowercase SHA-256 values")
        return value


class RulePackageTerm(_PackageModel):
    academic_year: str
    term_number: int = Field(strict=True, ge=1, le=3)

    @field_validator("academic_year")
    @classmethod
    def validate_academic_year(cls, value: str) -> str:
        match = _ACADEMIC_YEAR.fullmatch(value)
        if match is None or int(match[2]) != int(match[1]) + 1:
            raise ValueError("academic_year must use consecutive YYYY-YYYY")
        return value


class RulePackageThreshold(_PackageModel):
    pattern_type: PatternStream
    min_support: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    min_confidence: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    min_lift: Decimal = Field(gt=1, max_digits=8, decimal_places=4)
    min_term_recurrence: int = Field(strict=True, ge=1)
    training_term_count: int = Field(strict=True, ge=0)
    earliest_academic_year: str

    @field_validator("earliest_academic_year")
    @classmethod
    def validate_earliest_academic_year(cls, value: str) -> str:
        return RulePackageTerm.validate_academic_year(value)


class RulePackageItem(_PackageModel):
    kind: RuleItemKind
    value: str

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("rule item values must be non-blank and trimmed")
        return value

    @model_validator(mode="after")
    def validate_kind_value(self) -> "RulePackageItem":
        if self.kind == "faculty_id":
            if str(UUID(self.value)) != self.value:
                raise ValueError("faculty_id must use canonical UUID text")
        limits = {
            "subject_code": 20,
            "room_code": 20,
            "meeting_days": 10,
            "time_slot": 20,
        }
        limit = limits.get(self.kind)
        if limit is not None and len(self.value) > limit:
            raise ValueError(f"{self.kind} exceeds the backend column length {limit}")
        return self


class RulePackageRule(_PackageModel):
    pattern_type: PatternStream
    research_faculty_key: UUID
    faculty_name: str = Field(min_length=1, max_length=150)
    antecedent: tuple[RulePackageItem, ...]
    consequent: tuple[RulePackageItem, ...]
    support: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    confidence: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    lift: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    term_recurrence: int = Field(strict=True, ge=1)
    guidance_score: Decimal = Field(
        ge=0, le=1, max_digits=5, decimal_places=4
    )

    @field_validator("faculty_name")
    @classmethod
    def validate_faculty_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("faculty_name must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_stream_shape(self) -> "RulePackageRule":
        expected_antecedent, expected_consequent = _STREAM_ITEMS[self.pattern_type]
        actual_antecedent = tuple(item.kind for item in self.antecedent)
        actual_consequent = tuple(item.kind for item in self.consequent)
        if actual_antecedent != expected_antecedent:
            raise ValueError(
                f"{self.pattern_type} antecedent must be {expected_antecedent}"
            )
        if actual_consequent != expected_consequent:
            raise ValueError(
                f"{self.pattern_type} consequent must be {expected_consequent}"
            )
        return self

    @property
    def item_values(self) -> dict[str, str]:
        return {item.kind: item.value for item in (*self.antecedent, *self.consequent)}

    @property
    def identity(self) -> tuple[str, ...]:
        values = self.item_values
        return (
            self.pattern_type.value,
            values["faculty_id"],
            values.get("subject_code", ""),
            values.get("room_code", ""),
            values.get("meeting_days", ""),
            values.get("time_slot", ""),
        )


class CandidateRulePackage(_PackageModel):
    schema_version: Literal[1]
    traceability: Literal["CHR-100 / CHR-COLAB-004"]
    candidate_status: Literal["draft"]
    metadata: RulePackageMetadata
    training_terms: tuple[RulePackageTerm, ...]
    thresholds: tuple[RulePackageThreshold, ...]
    rules: tuple[RulePackageRule, ...]

    @model_validator(mode="after")
    def validate_candidate(self) -> "CandidateRulePackage":
        if not self.training_terms:
            raise ValueError("candidate package must include at least one training term")
        term_keys = [(term.academic_year, term.term_number) for term in self.training_terms]
        if len(term_keys) != len(set(term_keys)):
            raise ValueError("candidate package contains duplicate training terms")

        streams = [threshold.pattern_type for threshold in self.thresholds]
        if len(streams) != len(PatternStream) or set(streams) != set(PatternStream):
            raise ValueError("candidate package must define each threshold stream once")

        policy = HistoricalInclusionPolicy()
        for threshold in self.thresholds:
            expected = policy.earliest_academic_year(threshold.pattern_type)
            if threshold.earliest_academic_year != expected:
                raise ValueError(
                    f"{threshold.pattern_type} earliest_academic_year must be {expected}"
                )
            eligible_term_count = sum(
                policy.is_eligible(threshold.pattern_type, term.academic_year)
                for term in self.training_terms
            )
            if threshold.training_term_count > eligible_term_count:
                raise ValueError(
                    f"{threshold.pattern_type} training_term_count exceeds its "
                    "eligible training terms"
                )

        if not self.rules:
            raise ValueError("candidate package must contain at least one rule")
        identities = [rule.identity for rule in self.rules]
        if len(identities) != len(set(identities)):
            raise ValueError("candidate package contains duplicate rules")

        threshold_by_stream = {
            threshold.pattern_type: threshold for threshold in self.thresholds
        }
        faculty_mappings: dict[UUID, tuple[str, str]] = {}
        for rule in self.rules:
            mapping = (rule.faculty_name, rule.item_values["faculty_id"])
            previous = faculty_mappings.setdefault(rule.research_faculty_key, mapping)
            if previous != mapping:
                raise ValueError(
                    "one research_faculty_key cannot map to multiple faculty records"
                )

            validate_rule_against_threshold(
                rule, threshold_by_stream[rule.pattern_type]
            )
        return self


def validate_rule_against_threshold(
    rule: RulePackageRule, threshold: RulePackageThreshold
) -> None:
    """Apply the cross-field checks a packaged rule must pass for its stream."""
    if rule.support < threshold.min_support:
        raise ValueError(f"{rule.pattern_type} rule is below min_support")
    if rule.confidence < threshold.min_confidence:
        raise ValueError(f"{rule.pattern_type} rule is below min_confidence")
    if rule.lift < threshold.min_lift:
        raise ValueError(f"{rule.pattern_type} rule is below min_lift")
    if rule.term_recurrence < threshold.min_term_recurrence:
        raise ValueError(f"{rule.pattern_type} rule is below min_term_recurrence")

    training_term_count = threshold.training_term_count
    if training_term_count < 1:
        raise ValueError(f"{rule.pattern_type} rule has no mined training terms")
    if rule.term_recurrence > training_term_count:
        raise ValueError(
            f"{rule.pattern_type} term_recurrence exceeds its training terms"
        )
    expected_score = guidance_score(
        rule.confidence,
        rule.term_recurrence,
        training_term_count,
    )
    if rule.guidance_score != expected_score:
        raise ValueError(
            f"{rule.pattern_type} guidance_score must be {expected_score}"
        )


def load_rule_package(path: Path) -> CandidateRulePackage:
    return CandidateRulePackage.model_validate_json(path.read_text(encoding="utf-8"))


def write_rule_package(package: CandidateRulePackage, path: Path) -> None:
    payload = package.model_dump(mode="json")
    # Packages without reviewed aliases keep their pre-CHR-133 bytes.
    for field in ("reference_aliases_sha256", "excluded_rule_counts"):
        if payload["metadata"][field] is None:
            del payload["metadata"][field]
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
