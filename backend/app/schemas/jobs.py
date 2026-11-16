"""CHR-31 / CHR-WORKER-001 public job API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.ga.config import validate_meeting_day_patterns


class GAConfigParameters(BaseModel):
    """Stored parameters, including settings accepted before calendar validation."""

    model_config = ConfigDict(extra="forbid")

    population_size: int = Field(default=50, gt=0)
    max_generations: int = Field(default=100, gt=0)
    crossover_rate: float = Field(default=0.8, ge=0.0, le=1.0)
    mutation_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    gene_mutation_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    tournament_size: int = Field(default=3, ge=2)
    elite_count: int = Field(default=1, ge=0)
    stagnation_limit: int | None = Field(default=20, gt=0)
    random_seed: int = 0
    meeting_day_patterns: tuple[str, ...] = Field(default=("MW", "TTh", "F"), min_length=1)

    @model_validator(mode="after")
    def validate_population_dependent_values(self) -> "GAConfigParameters":
        if self.tournament_size > self.population_size:
            raise ValueError("tournament_size must not exceed population_size")
        if self.elite_count >= self.population_size:
            raise ValueError("elite_count must be smaller than population_size")
        if any(not pattern for pattern in self.meeting_day_patterns):
            raise ValueError("meeting_day_patterns must not contain blank values")
        return self


class GAConfigRequest(GAConfigParameters):
    """New saves and overrides must fit the current teaching calendar."""

    @field_validator("meeting_day_patterns")
    @classmethod
    def validate_meeting_days(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        return validate_meeting_day_patterns(patterns)


class ContextProposalAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID
    faculty_id: UUID
    room_code: str = Field(min_length=1, max_length=20)
    time_slot_id: UUID
    meeting_days: str = Field(min_length=1, max_length=10)


class ContextProposalEdits(BaseModel):
    """A context run's proposal as edited by hand in review."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    assignments: list[ContextProposalAssignment] = Field(max_length=500)


class SubmitGAJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_id: UUID
    block_id: UUID | None = Field(default=None, description="Selected Block for per-Block generation")
    base_schedule_id: UUID | None = Field(default=None, description="Base Schedule for locked assignments")
    context_job_ids: list[UUID] = Field(
        default_factory=list,
        max_length=500,
        description=(
            "Completed per-Block runs of other Blocks against the same base "
            "Schedule, still awaiting review. Their candidates are held as "
            "extra locked context so a sequential generate-all batch does not "
            "double-book instructors or rooms across its own proposals."
        ),
    )
    context_edits: list[ContextProposalEdits] = Field(
        default_factory=list,
        max_length=500,
        description=(
            "The whole proposal of a context run edited by hand in review. It "
            "is held as context in place of the run's original candidate, so "
            "a class moved in review is not double-booked by the next Block."
        ),
    )
    config: GAConfigRequest | None = Field(
        default=None,
        description="Administrator override; omitted runs use the active saved configuration",
    )

    @model_validator(mode="after")
    def validate_block_generation(self) -> "SubmitGAJobRequest":
        if self.block_id is not None and self.base_schedule_id is None:
            raise ValueError("base_schedule_id is required when block_id is provided")
        if self.context_job_ids and self.block_id is None:
            raise ValueError("context_job_ids requires block_id")
        if len(set(self.context_job_ids)) != len(self.context_job_ids):
            raise ValueError("context_job_ids must contain unique values")
        edited = [item.job_id for item in self.context_edits]
        if len(set(edited)) != len(edited):
            raise ValueError("context_edits must name each run once")
        if not set(edited) <= set(self.context_job_ids):
            raise ValueError("context_edits must name runs listed in context_job_ids")
        return self


class ARMStreamThresholdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Match RuleSetThreshold's NUMERIC columns without rounding queued inputs.
    min_support: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    min_confidence: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    min_lift: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    min_term_recurrence: int = Field(ge=1)

    @field_validator("min_support", "min_confidence", "min_lift")
    @classmethod
    def validate_exact_scale(cls, value: Decimal) -> Decimal:
        # PostgreSQL cannot parse arbitrarily large zero exponents from the codec.
        if value.is_zero():
            return Decimal(0)
        # Pydantic's digit check normalizes with context precision, hiding long tails.
        _, digits, exponent = value.as_tuple()
        if exponent < -4 and any(digits[exponent + 4:]):
            raise ValueError("threshold must be exactly representable with four decimal places")
        return value


class ARMThresholdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teacher_subject: ARMStreamThresholdsRequest
    teacher_day: ARMStreamThresholdsRequest
    teacher_time: ARMStreamThresholdsRequest
    teacher_subject_room: ARMStreamThresholdsRequest


class SubmitARMJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_snapshot_ids: list[UUID] = Field(
        min_length=1,
        description="Unique eligible historical snapshot IDs to mine from",
    )
    thresholds: ARMThresholdsRequest
    # Bounded by `RuleSet.notes`, which is VARCHAR(500): anything longer would be
    # accepted here and then fail when the worker persists the draft.
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_unique_snapshot_ids(self) -> "SubmitARMJobRequest":
        if len(self.selected_snapshot_ids) != len(set(self.selected_snapshot_ids)):
            raise ValueError("selected_snapshot_ids must contain unique values")
        return self


JobKind = Literal["ga", "arm", "evaluation"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "timed_out"]


class JobAcceptedResponse(BaseModel):
    job_id: UUID
    kind: JobKind
    status: JobStatus


class JobErrorResponse(BaseModel):
    code: str
    message: str


class EvaluationBatchReference(BaseModel):
    """CHR-172 / CHR-FIX-012: locates retained evidence after a failed evaluation."""

    evaluation_batch_id: UUID
    completed_run_count: int
    planned_run_count: int


class JobGuidanceReference(BaseModel):
    """CHR-146 / CHR-ARM-009: the rule set frozen into a GA run at submission."""

    mode: Literal["guided", "unguided"]
    rule_set_id: UUID | None = None
    rule_set_version: int | None = None


class JobScope(BaseModel):
    """CHR-174 / CHR-SCH-014: which term (and Block) a GA run scheduled."""

    term_id: UUID | None = None
    academic_year: str | None = None
    term_number: int | None = None
    block_id: UUID | None = None


class JobHistoryItem(JobAcceptedResponse):
    """One history row; results and raw request inputs stay on the detail view."""

    model_config = ConfigDict(extra="forbid")

    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: JobErrorResponse | None = None
    guidance: JobGuidanceReference | None = None
    scope: JobScope | None = None
    # e.g. "2601CS1BV101": AY start year, term, program, Block, rule set
    # version (V0 unguided), run number. None for ARM and unreadable payloads.
    display_id: str | None = None


class JobStatusResponse(JobHistoryItem):
    """Safe durable state; claim leases and raw request inputs are never exposed.

    ``config`` is only the frozen GA parameter snapshot (including the seed),
    never the scheduling problem or historical records it ran over.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    result: dict[str, Any] | None = None
    evaluation_batch: EvaluationBatchReference | None = None
    config: dict[str, Any] | None = None


class JobHistoryPage(BaseModel):
    items: list[JobHistoryItem]
    total: int
    limit: int
    offset: int


class DeleteJobsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: list[UUID] = Field(min_length=1, max_length=100)


class SkippedJob(BaseModel):
    job_id: UUID
    reason: Literal["not_found", "active", "in_use"]


class DeleteJobsResponse(BaseModel):
    deleted: list[UUID]
    skipped: list[SkippedJob]
