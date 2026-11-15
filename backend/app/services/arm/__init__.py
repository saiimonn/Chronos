"""ARM mining, historical guidance, and candidate RuleSet services."""

from importlib import import_module

from app.services.arm.mining import (
    CandidateRuleSet,
    MinedRule,
    MiningSkipReason,
    MiningThresholds,
    RuleMiner,
    StreamMiningResult,
    guidance_score,
)
from app.services.arm.preprocessing import (
    ARMTransaction,
    HistoricalInclusionPolicy,
    HistoricalOffering,
    HistoricalPartition,
    HistoricalTerm,
    MiningMode,
    PatternStream,
    StreamTrainingData,
    TransactionBuilder,
    prepare_training_data,
    reject_duplicate_record_ids,
    time_comparison_token,
)


# Keep database-backed services lazy so the file-only Colab packager has no DB layer.
def __getattr__(name: str):
    if name in {
        "DatabaseRuleProvider",
        "FixtureRuleProvider",
        "GuidanceKeyCollisionError",
        "GuidanceRule",
        "HistoricalGuidance",
        "HistoricalRuleProvider",
        "NoActiveRuleSetError",
        "RuleSetNotFoundError",
    }:
        return getattr(import_module("app.services.arm.providers"), name)
    if name in {
        "MiningProducedNoRulesError",
        "MiningRunSummary",
        "RuleMiningService",
        "StreamSummary",
        "load_historical_terms",
        "load_historical_terms_by_ids",
    }:
        return getattr(import_module("app.services.arm.service"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ARMTransaction",
    "CandidateRuleSet",
    "DatabaseRuleProvider",
    "FixtureRuleProvider",
    "GuidanceKeyCollisionError",
    "GuidanceRule",
    "HistoricalGuidance",
    "HistoricalInclusionPolicy",
    "HistoricalOffering",
    "HistoricalPartition",
    "HistoricalRuleProvider",
    "HistoricalTerm",
    "MinedRule",
    "MiningProducedNoRulesError",
    "MiningRunSummary",
    "MiningSkipReason",
    "MiningThresholds",
    "NoActiveRuleSetError",
    "PatternStream",
    "RuleMiner",
    "RuleMiningService",
    "RuleSetNotFoundError",
    "StreamMiningResult",
    "StreamSummary",
    "StreamTrainingData",
    "TransactionBuilder",
    "guidance_score",
    "load_historical_terms",
    "load_historical_terms_by_ids",
    "MiningMode",
    "prepare_training_data",
    "reject_duplicate_record_ids",
    "time_comparison_token",
]
