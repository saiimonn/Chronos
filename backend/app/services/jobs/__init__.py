"""Local spawn-process execution boundary for CHR-31 / CHR-WORKER-001."""

from app.services.jobs.codecs import (
    CODEC_VERSION,
    decode_block_generation_context,
    decode_ga_config,
    decode_historical_terms,
    decode_mining_thresholds,
    decode_scheduling_problem,
    encode_arm_payload,
    encode_block_generation_context,
    encode_block_ga_payload,
    encode_ga_config,
    encode_ga_payload,
    encode_historical_terms,
    encode_mining_thresholds,
    encode_scheduling_problem,
)
from app.services.jobs.handlers import dispatch, run_arm, run_ga
from app.services.jobs.supervisor import JobExecutionResult, JobOutcome, execute_job

__all__ = [
    "CODEC_VERSION",
    "JobExecutionResult",
    "JobOutcome",
    "decode_block_generation_context",
    "decode_ga_config",
    "decode_historical_terms",
    "decode_mining_thresholds",
    "decode_scheduling_problem",
    "dispatch",
    "encode_arm_payload",
    "encode_block_generation_context",
    "encode_block_ga_payload",
    "encode_ga_config",
    "encode_ga_payload",
    "encode_historical_terms",
    "encode_mining_thresholds",
    "encode_scheduling_problem",
    "execute_job",
    "run_arm",
    "run_ga",
]
