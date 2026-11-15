"""CHR-115 / CHR-EVAL-009 matched-evaluation submission contract."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.jobs import GAConfigRequest
from app.services.evaluation import MATCHED_PAIR_COUNT, SEED_MAX, SEED_MIN


class SubmitEvaluationRequest(BaseModel):
    """One term, one Rule Set and one GA config shared by all ten pairs."""

    model_config = ConfigDict(extra="forbid")

    term_id: UUID
    rule_set_id: UUID
    seeds: list[int] = Field(
        default_factory=lambda: list(range(MATCHED_PAIR_COUNT)),
        description="Ten distinct 64-bit seeds; each is used by one guided and one unguided run",
    )
    config: GAConfigRequest = Field(default_factory=GAConfigRequest)

    @field_validator("seeds")
    @classmethod
    def validate_seeds(cls, seeds: list[int]) -> list[int]:
        if len(seeds) != MATCHED_PAIR_COUNT:
            raise ValueError(f"exactly {MATCHED_PAIR_COUNT} seeds are required")
        if len(set(seeds)) != MATCHED_PAIR_COUNT:
            raise ValueError("seeds must be unique")
        if any(not SEED_MIN <= seed <= SEED_MAX for seed in seeds):
            raise ValueError("seeds must fit a signed 64-bit integer")
        return seeds

    @field_validator("config")
    @classmethod
    def validate_config_seed(cls, config: GAConfigRequest) -> GAConfigRequest:
        # Each run replaces the seed, but the base config is exported with the
        # batch, and the CHR-101 analysis rejects a seed outside 64 bits.
        if not SEED_MIN <= config.random_seed <= SEED_MAX:
            raise ValueError("config.random_seed must fit a signed 64-bit integer")
        return config
