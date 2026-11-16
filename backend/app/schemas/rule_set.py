from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TermBrief(BaseModel):
    """Compact term representation nested inside rule set responses."""

    model_config = ConfigDict(from_attributes=True)

    term_id: UUID
    academic_year: str
    term_number: int


class SourceSnapshotBrief(BaseModel):
    """One immutable historical snapshot a rule set was mined from."""

    model_config = ConfigDict(from_attributes=True)

    snapshot_id: UUID
    academic_year: str
    term_number: int


class RuleSetThresholdResponse(BaseModel):
    """One Apriori threshold row for a single pattern stream."""

    model_config = ConfigDict(from_attributes=True)

    pattern_type: str
    min_support: Decimal
    min_confidence: Decimal
    min_lift: Decimal
    min_term_recurrence: int
    earliest_academic_year: str


class RuleSetStreamRules(BaseModel):
    """Aggregated rule count for one pattern type inside a rule set."""

    pattern_type: str
    rule_count: int


class RuleSetRuleResponse(BaseModel):
    """One extracted rule as the administrator reviews it before activation.

    `antecedent` and `consequent` are display labels in rule order: the faculty
    member (plus the subject for teacher_subject_room) implies the consequent
    the stream defines. The raw ARM metrics travel alongside so the reviewer
    sees what the guidance score was derived from.
    """

    rule_id: UUID
    pattern_type: str
    faculty_id: UUID
    antecedent: list[str]
    consequent: list[str]
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int
    guidance_score: Decimal


class RuleSetRulePage(BaseModel):
    """One page of a single stream's rules."""

    pattern_type: str
    total: int
    limit: int
    offset: int
    items: list[RuleSetRuleResponse]


class RuleSetFacultyCoverageItem(BaseModel):
    """One teacher's best candidate pattern in a stream and how it fared."""

    source_faculty_key: str
    faculty_id: UUID | None
    faculty_name: str
    on_roster: bool
    is_active: bool | None
    has_rule: bool
    subject_code: str | None
    room_code: str | None
    meeting_days: str | None
    time_slot: str | None
    offering_count: int | None
    support: Decimal
    confidence: Decimal
    lift: Decimal
    term_recurrence: int
    meets_support: bool
    meets_confidence: bool
    meets_lift: bool
    meets_term_recurrence: bool
    # Teacher-day only: the GA day pattern (MW, TTh, F) the teacher taught most
    # in the version's training terms, with its class count and their total.
    usual_days: str | None = None
    usual_days_count: int | None = None
    usual_days_total: int | None = None


class RuleSetFacultyCoverage(BaseModel):
    """Every teacher in one stream's history, with and without rules."""

    pattern_type: str
    metrics_recorded: bool
    # Distinct teachers with a rule in any stream of the version.
    version_with_rule_count: int
    teacher_count: int
    with_rule_count: int
    without_rule_count: int
    items: list[RuleSetFacultyCoverageItem]


class RuleSetListResponse(BaseModel):
    """Summary returned for each rule set in the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    rule_set_id: UUID
    version: int
    version_label: str | None = None
    status: str
    generated_at: datetime
    activated_at: datetime | None
    notes: str | None
    term_count: int
    rule_count: int


class RuleSetDetailResponse(BaseModel):
    """Full detail returned for a single rule set."""

    model_config = ConfigDict(from_attributes=True)

    rule_set_id: UUID
    version: int
    version_label: str | None = None
    status: str
    generated_at: datetime
    activated_at: datetime | None
    notes: str | None
    included_terms: list[TermBrief]
    thresholds: list[RuleSetThresholdResponse]
    streams: list[RuleSetStreamRules]
    # The ARM job that published this version; set for every mined version,
    # including those published before the provenance fields below existed.
    job_id: UUID | None
    # Mining provenance; absent on versions published before CHR-ARM-008.
    source_job_id: UUID | None
    requested_by_user_id: UUID | None
    # Display name for the requester; None when unrecorded or the account is gone.
    requested_by_username: str | None
    source_snapshots: list[SourceSnapshotBrief]
    mining_summary: dict | None


class HistoricalTermOptionResponse(BaseModel):
    """One captured historical term offered as an input to a new mining run.

    `eligible_streams` is the subset of the four pattern streams whose earliest
    permitted academic year this term satisfies. The administrator picks terms
    before any mining happens, so the window is published here rather than left
    for the run to discover and silently drop.
    """

    model_config = ConfigDict(from_attributes=True)

    snapshot_id: UUID
    academic_year: str
    term_number: int
    captured_at: datetime
    record_count: int
    eligible_streams: list[str]
