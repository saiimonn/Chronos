"""CHR-31 / CHR-WORKER-001 process-boundary codec tests."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import BlockGenerationContext, BlockSectionDTO, LockedAssignment
from app.services.arm import (
    HistoricalOffering,
    HistoricalTerm,
    MiningThresholds,
    PatternStream,
)
from app.services.ga.config import GAConfig
from app.services.jobs import (
    CODEC_VERSION,
    decode_ga_config,
    decode_historical_terms,
    decode_mining_thresholds,
    decode_scheduling_problem,
    encode_block_ga_payload,
    encode_ga_config,
    encode_ga_payload,
    encode_historical_terms,
    encode_mining_thresholds,
    encode_scheduling_problem,
)
from app.services.jobs.codecs import (
    decode_block_generation_context,
    encode_block_generation_context,
    require_version,
)
from app.tests.support.ga import make_ga_problem
from app.tests.support.scheduling import (
    FACULTY_ID,
    TERM_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
)


def test_scheduling_problem_and_ga_config_round_trip_losslessly():
    problem = make_ga_problem()
    config = GAConfig(
        population_size=8,
        max_generations=3,
        tournament_size=2,
        elite_count=1,
        stagnation_limit=None,
        random_seed=41,
        meeting_day_patterns=("MW", "TTh"),
    )

    encoded_problem = encode_scheduling_problem(problem)

    assert decode_scheduling_problem(encoded_problem) == problem
    assert {item["subject_type"] for item in encoded_problem["subjects"]} == {
        "LAB",
        "LEC",
    }
    assert all("duration_minutes" not in item for item in encoded_problem["subjects"])
    assert decode_ga_config(encode_ga_config(config)) == config
    assert CODEC_VERSION == 7
    assert encode_ga_payload(problem, config)["version"] == CODEC_VERSION


def test_faculty_status_round_trips_and_older_payloads_read_as_active():
    base = make_ga_problem()
    problem = replace(
        base, faculty=(replace(base.faculty[0], is_active=False),) + base.faculty[1:]
    )
    encoded = encode_scheduling_problem(problem)

    assert decode_scheduling_problem(encoded) == problem
    legacy = {
        **encoded,
        "faculty": [
            {key: value for key, value in item.items() if key != "is_active"}
            for item in encoded["faculty"]
        ],
    }
    assert all(item.is_active for item in decode_scheduling_problem(legacy).faculty)


def test_legacy_requires_lab_job_subjects_still_decode():
    problem = make_ga_problem()
    encoded = encode_scheduling_problem(problem)
    legacy = {
        **encoded,
        "subjects": [
            {
                **{key: value for key, value in item.items() if key != "subject_type"},
                "requires_lab": item["subject_type"] == "LAB",
            }
            for item in encoded["subjects"]
        ],
    }

    legacy_payload = {"version": 1, "problem": legacy}

    require_version(legacy_payload)
    assert decode_scheduling_problem(legacy_payload["problem"]) == problem


def test_legacy_semester_job_payloads_still_decode():
    problem = make_ga_problem()
    encoded = encode_scheduling_problem(problem)
    term = {
        key: value for key, value in encoded["term"].items() if key != "term_number"
    }
    legacy = {**encoded, "term": {**term, "semester": problem.term.term_number}}

    # 3 is the version that shipped before the rename; jobs queued under it are
    # still in the table and have to survive the upgrade.
    legacy_payload = {"version": 3, "problem": legacy}

    require_version(legacy_payload)
    assert decode_scheduling_problem(legacy_payload["problem"]) == problem


def test_offering_block_ids_survive_a_round_trip():
    problem = make_ga_problem()

    encoded = encode_scheduling_problem(problem)

    assert problem.offering_block_ids
    assert encoded["offering_block_ids"] == {
        str(offering_id): [str(block_id) for block_id in block_ids]
        for offering_id, block_ids in problem.offering_block_ids.items()
    }
    assert (
        decode_scheduling_problem(encoded).offering_block_ids
        == problem.offering_block_ids
    )


def test_legacy_payloads_without_offering_block_ids_still_decode():
    problem = make_ga_problem()
    encoded = encode_scheduling_problem(problem)
    # 4 is the version that shipped before the field existed, so its payloads
    # carry no offering_block_ids key at all.
    legacy = {
        key: value for key, value in encoded.items() if key != "offering_block_ids"
    }

    legacy_payload = {"version": 4, "problem": legacy}

    require_version(legacy_payload)
    assert decode_scheduling_problem(legacy_payload["problem"]) == replace(
        problem, offering_block_ids={}
    )


@pytest.mark.parametrize("version", (None, 0, 999, True))
def test_unknown_job_payload_versions_are_rejected(version):
    with pytest.raises(ValueError, match="unsupported job payload version"):
        require_version({"version": version})


def test_historical_terms_and_per_stream_thresholds_round_trip_losslessly():
    terms = (
        HistoricalTerm(
            snapshot_id=UUID("10000000-0000-0000-0000-000000000001"),
            academic_year="2023-2024",
            term_number=1,
            offerings=(
                HistoricalOffering(
                    historical_record_id=UUID("20000000-0000-0000-0000-000000000001"),
                    faculty_id=UUID("30000000-0000-0000-0000-000000000001"),
                    subject_code="<SUBJECT-1>",
                    meeting_days="MWF",
                    time_slot="08:00-09:00",
                    room_code=None,
                ),
            ),
        ),
    )
    thresholds = {
        stream: MiningThresholds(
            min_support=Decimal("0.1250"),
            min_confidence=Decimal("0.5000"),
            min_lift=Decimal("1.0000"),
            min_term_recurrence=index + 1,
        )
        for index, stream in enumerate(PatternStream)
    }

    assert decode_historical_terms(encode_historical_terms(terms)) == terms
    assert decode_mining_thresholds(encode_mining_thresholds(thresholds)) == thresholds


def _make_block_generation_context() -> BlockGenerationContext:
    problem = make_scheduling_problem()
    selected_block = BlockSectionDTO(
        block_id=UUID("00000000-0000-0000-0000-000000000006"),
        term_id=TERM_ID,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter="A",
        capacity=20,
    )
    locked = (
        LockedAssignment(
            class_id=UUID("00000000-0000-0000-0000-000000000030"),
            faculty_id=FACULTY_ID,
            time_slot_id=TIME_SLOT_ID,
            room_code="<ROOM-1>",
            meeting_days="MWF",
            block_id=UUID("00000000-0000-0000-0000-000000000020"),
            offering_id=UUID("00000000-0000-0000-0000-000000000040"),
            units=3,
        ),
    )
    return BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000006"),
        selected_block=selected_block,
        selected_classes=problem.classes,
        locked_assignments=locked,
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=UUID("00000000-0000-0000-0000-000000000005"),
    )


def test_block_generation_context_round_trip_losslessly():
    context = _make_block_generation_context()

    encoded = encode_block_generation_context(context)
    decoded = decode_block_generation_context(encoded)

    assert decoded == context
    assert encoded["version"] == CODEC_VERSION
    assert decoded.selected_block_id == context.selected_block_id
    assert decoded.base_schedule_id == context.base_schedule_id
    assert len(decoded.locked_assignments) == 1
    assert decoded.locked_assignments[0].block_id != decoded.selected_block_id


def test_legacy_locked_assignments_without_offering_identity_still_decode():
    encoded = encode_block_generation_context(_make_block_generation_context())
    for item in encoded["context"]["locked_assignments"]:
        del item["offering_id"], item["units"]

    locked = decode_block_generation_context(encoded).locked_assignments[0]

    assert (locked.offering_id, locked.units) == (None, 0)


def test_block_generation_context_with_no_locked_assignments():
    context = replace(
        _make_block_generation_context(),
        locked_assignments=(),
        base_schedule_id=None,
    )

    encoded = encode_block_generation_context(context)
    decoded = decode_block_generation_context(encoded)

    assert decoded == context
    assert decoded.locked_assignments == ()
    assert decoded.base_schedule_id is None


def test_block_generation_context_preserves_offering_block_ids():
    context = _make_block_generation_context()

    encoded = encode_block_generation_context(context)
    decoded = decode_block_generation_context(encoded)

    assert decoded.offering_block_ids == context.offering_block_ids


def test_block_ga_payload_round_trip_losslessly():
    context = _make_block_generation_context()
    config = GAConfig(
        population_size=4,
        max_generations=2,
        tournament_size=2,
        elite_count=1,
        random_seed=17,
    )

    encoded = encode_block_ga_payload(context, config)

    assert encoded["version"] == CODEC_VERSION
    assert "block_context" in encoded
    assert "config" in encoded
    decoded = decode_block_generation_context(encoded["block_context"])
    assert decoded == context
