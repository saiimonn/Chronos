"""CHR-31 / CHR-WORKER-001 and CHR-126 / CHR-ARM-006 handler regressions."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch
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
    JobOutcome,
    encode_arm_payload,
    encode_block_ga_payload,
    encode_ga_payload,
    run_arm,
    run_ga,
)
from app.services.jobs.outcomes import DOMAIN_OUTCOMES, JobDomainError
from app.tests.support.ga import make_ga_problem
from app.tests.support.scheduling import (
    FACULTY_ID,
    TERM_ID,
    TIME_SLOT_ID,
    make_scheduling_problem,
)


def _historical_terms() -> tuple[HistoricalTerm, ...]:
    return tuple(
        HistoricalTerm(
            snapshot_id=UUID(f"10000000-0000-0000-0000-{index:012d}"),
            academic_year=f"{2023 + index}-{2024 + index}",
            term_number=1,
            offerings=(
                HistoricalOffering(
                    historical_record_id=UUID(
                        f"20000000-0000-0000-0000-{index:012d}"
                    ),
                    faculty_id=UUID("30000000-0000-0000-0000-000000000001"),
                    subject_code="<SUBJECT-1>",
                    meeting_days="MWF",
                    time_slot="08:00-09:00",
                    room_code="<ROOM-1>",
                ),
            ),
        )
        for index in range(1, 8)
    )


def _ga_payload(*, job_id: UUID | None = None) -> dict:
    payload = encode_ga_payload(
        make_ga_problem(include_unstaffable=False),
        GAConfig(
            population_size=4,
            max_generations=2,
            tournament_size=2,
            elite_count=1,
            random_seed=17,
        ),
    )
    payload["job_id"] = str(job_id or UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    payload["claim_token"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
    return payload


def _publish(_session, candidate, *, claim_token, result):
    return SimpleNamespace(
        schedule_id=candidate.schedule_id, publication_result=result
    )


def _arm_payload(thresholds: dict) -> dict:
    return {
        **encode_arm_payload(_historical_terms(), thresholds),
        "job_id": "12600000-0000-0000-0000-000000000001",
        "claim_token": "12600000-0000-0000-0000-000000000002",
        "requested_by": "12600000-0000-0000-0000-000000000003",
    }


def test_ga_handler_is_deterministic_except_for_elapsed_time_and_schedule_id():
    mock_session = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__ = MagicMock(return_value=mock_session)
    mock_context.__exit__ = MagicMock(return_value=False)

    with patch("app.db.session.SessionLocal", return_value=mock_context), \
         patch("app.services.schedules.find_published_schedule", return_value=None), \
         patch("app.services.schedules.persist_candidate", side_effect=_publish):
        first = run_ga(_ga_payload())
        second = run_ga(_ga_payload())

    first["metrics"].pop("elapsed_seconds")
    second["metrics"].pop("elapsed_seconds")
    first["candidate"].pop("schedule_id")
    second["candidate"].pop("schedule_id")

    assert first == second
    assert first["metrics"]["assignment_completeness"] == 1.0
    assert first["metrics"]["historical_pattern_attainment"] == 0.0


def test_arm_handler_serializes_all_four_streams_rules_and_metrics():
    thresholds = {
        stream: MiningThresholds(
            min_support=Decimal("0.1"),
            min_confidence=Decimal("0.1"),
            min_lift=Decimal("0"),
            min_term_recurrence=1,
        )
        for stream in PatternStream
    }

    from app.models import RuleSet
    from app.services.jobs.handlers import _persist_draft_ruleset

    payload = _arm_payload(thresholds)
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
        patch(
            "app.services.jobs.handlers._persist_draft_ruleset",
            wraps=_persist_draft_ruleset,
        ) as persist,
    ):
        session = local.begin.return_value.__enter__.return_value
        repository.return_value.lock_live_claim.return_value = True
        # No prior publication, no existing ID, then the initial version.
        scalar_results = iter((None, None, 0))

        def scalar(statement):
            if list(statement.selected_columns.keys()) == ["publication_result"]:
                return session.add.call_args_list[0].args[0].publication_result
            return next(scalar_results)

        session.scalar.side_effect = scalar
        # Catalog filtering keeps everything but the room rule.
        # The roster is read as (faculty_id, full_name) pairs.
        session.execute.return_value.tuples.return_value.all.return_value = [
            (_historical_terms()[0].offerings[0].faculty_id, "<FACULTY>")
        ]
        session.scalars.return_value.all.side_effect = [
            ["<SUBJECT-1>"],
            [], [], [], [],
        ]
        result = run_arm(payload)

    assert persist.call_args.kwargs["job_id"] == UUID(payload["job_id"])
    assert persist.call_args.kwargs["claim_token"] == UUID(payload["claim_token"])
    assert persist.call_args.kwargs["requested_by_user_id"] == UUID(
        payload["requested_by"]
    )
    rule_set = session.add.call_args_list[0].args[0]
    assert isinstance(rule_set, RuleSet)
    assert rule_set.source_job_id == UUID(payload["job_id"])
    assert rule_set.requested_by_user_id == UUID(payload["requested_by"])
    assert result["rule_set_id"] == str(rule_set.rule_set_id)
    assert rule_set.mining_summary["persisted_rule_count"] == 3
    assert [item["stream"] for item in result["streams"]] == [
        stream.value for stream in PatternStream
    ]
    assert result["metrics"]["stream_count"] == 4
    assert result["metrics"]["input_record_count"] == 7
    # Candidates before catalog filtering, then what the version actually holds.
    assert result["metrics"]["mined_rule_count"] == 4
    assert result["metrics"]["retained_rule_count"] == 3
    room = result["streams"][-1]
    assert (room["mined_rule_count"], room["retained_rule_count"]) == (1, 0)
    assert room["excluded_rule_counts"]["unknown_room"] == 1
    # Operational mining uses every selected eligible term, holdout included.
    assert all(item["training_term_count"] == 7 for item in result["streams"])
    assert all(item["rules"] for item in result["streams"])


@pytest.mark.parametrize(
    "identity",
    [{}, {"job_id": "invalid", "claim_token": "invalid"},
     {"job_id": None, "claim_token": None}],
)
def test_arm_handler_requires_execution_identity_before_mining(identity):
    from app.services.jobs.codecs import CODEC_VERSION

    with (
        patch("app.services.jobs.handlers.RuleMiner") as miner,
        pytest.raises((KeyError, ValueError, TypeError)),
    ):
        run_arm({"version": CODEC_VERSION, **identity})
    miner.assert_not_called()


def test_arm_handler_recovers_original_result_without_decoding_or_mining():
    payload = {key: value for key, value in _arm_payload({}).items() if key != "terms"}
    published = MagicMock()
    published.rule_set_id = UUID(int=126)
    original = {
        "rule_set_id": str(published.rule_set_id),
        "metrics": {"retained_rule_count": 3},
    }
    published.publication_result = original
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
        patch("app.services.jobs.handlers.RuleMiner") as miner,
        patch("app.services.jobs.handlers._persist_draft_ruleset") as persist,
    ):
        repository.return_value.lock_live_claim.return_value = True
        local.begin.return_value.__enter__.return_value.scalar.return_value = published
        assert run_arm(payload) is original

    miner.assert_not_called()
    persist.assert_not_called()


def test_arm_handler_rejects_invalid_claim_before_recovery_or_mining():
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
        patch("app.services.jobs.handlers.RuleMiner") as miner,
    ):
        repository.return_value.lock_live_claim.return_value = False
        with pytest.raises(ValueError, match="ARM publication requires a live worker claim"):
            run_arm(_arm_payload({}))

    local.begin.return_value.__enter__.return_value.scalar.assert_not_called()
    miner.assert_not_called()


def _unreachable_thresholds() -> dict:
    return {
        stream: MiningThresholds(
            min_support=Decimal("0.1"),
            min_confidence=Decimal("0.1"),
            min_lift=Decimal("0"),
            min_term_recurrence=99,
        )
        for stream in PatternStream
    }


def test_arm_handler_reports_no_retained_rules_as_a_domain_outcome():
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
        patch("app.services.jobs.handlers._persist_draft_ruleset") as persist,
    ):
        repository.return_value.lock_live_claim.return_value = True
        local.begin.return_value.__enter__.return_value.scalar.return_value = None
        with pytest.raises(JobDomainError) as raised:
            run_arm(_arm_payload(_unreachable_thresholds()))

    assert raised.value.code == "arm_no_rules_retained"
    persist.assert_not_called()


def test_arm_no_rules_outcome_survives_the_child_protocol():
    import json

    from app.services.jobs.supervisor import _child_main, _decode_message

    payload = _arm_payload(_unreachable_thresholds())
    send = MagicMock()
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
    ):
        repository.return_value.lock_live_claim.return_value = True
        local.begin.return_value.__enter__.return_value.scalar.return_value = None
        _child_main(send, "arm", json.dumps(payload).encode("utf-8"))

    result = _decode_message(send.send_bytes.call_args.args[0])

    assert result.outcome is JobOutcome.DOMAIN_ERROR
    assert result.code == "arm_no_rules_retained"
    assert result.error == DOMAIN_OUTCOMES["arm_no_rules_retained"]
    assert result.data is None


def test_arm_handler_rolls_back_when_catalog_filtering_leaves_no_rules():
    from app.models import ExtractedRule, RuleSet

    thresholds = {
        stream: replace(value, min_term_recurrence=1)
        for stream, value in _unreachable_thresholds().items()
    }
    with (
        patch("app.db.session.SessionLocal") as local,
        patch("app.repositories.jobs.JobRepository") as repository,
    ):
        context = local.begin.return_value
        session = context.__enter__.return_value
        session.scalar.side_effect = [None, None, 0]
        session.scalars.return_value.all.return_value = []
        repository.return_value.lock_live_claim.return_value = True
        with pytest.raises(ValueError, match="catalog filtering left no usable rules"):
            run_arm(_arm_payload(thresholds))

    rule_set = session.add.call_args_list[0].args[0]
    assert isinstance(rule_set, RuleSet)
    assert rule_set.publication_result is None
    assert not any(
        isinstance(call.args[0], ExtractedRule) for call in session.add.call_args_list
    )
    assert context.__exit__.call_args.args[0] is ValueError
    assert session.flush.call_count == 1


def test_ga_block_handler_is_deterministic():
    problem = make_ga_problem(include_unstaffable=False)
    assert len(problem.classes) > 1
    # Scope to exactly one class so the block run operates on a true subset of the term
    selected_classes = (problem.classes[0],)
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000006"),
        selected_block=BlockSectionDTO(
            block_id=UUID("00000000-0000-0000-0000-000000000006"),
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=selected_classes,
        locked_assignments=(
            LockedAssignment(
                class_id=UUID("00000000-0000-0000-0000-000000000030"),
                faculty_id=FACULTY_ID,
                time_slot_id=problem.time_slots[0].time_slot_id,
                room_code="<ROOM-1>",
                meeting_days="MWF",
                block_id=UUID("00000000-0000-0000-0000-000000000020"),
                offering_id=UUID("00000000-0000-0000-0000-000000000031"),
                units=3,
            ),
        ),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=UUID("00000000-0000-0000-0000-000000000005"),
    )
    config = GAConfig(
        population_size=4,
        max_generations=2,
        tournament_size=2,
        elite_count=1,
        random_seed=17,
    )
    payload = encode_block_ga_payload(context, config)

    first = run_ga(payload)
    second = run_ga(payload)
    first["metrics"].pop("elapsed_seconds")
    second["metrics"].pop("elapsed_seconds")
    first["candidate"].pop("schedule_id")
    second["candidate"].pop("schedule_id")

    assert first == second
    assert first["locks_enforced"] is True
    assert len(first["candidate"]["assignments"]) == 1
    assert first["metrics"]["required_class_count"] == 1
    assert first["metrics"]["assigned_class_count"] == 1
    assert first["metrics"]["assignment_completeness"] == 1.0


def test_ga_handler_routes_term_wide_when_no_block_context():
    problem = make_ga_problem(include_unstaffable=False)
    assert len(problem.classes) == 2
    config = GAConfig(
        population_size=4,
        max_generations=2,
        tournament_size=2,
        elite_count=1,
        random_seed=17,
    )
    payload = encode_ga_payload(problem, config)
    payload.update(
        job_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        claim_token="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab",
    )

    mock_session = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__ = MagicMock(return_value=mock_session)
    mock_context.__exit__ = MagicMock(return_value=False)

    with patch("app.db.session.SessionLocal", return_value=mock_context), \
         patch("app.services.schedules.find_published_schedule", return_value=None), \
         patch("app.services.schedules.persist_candidate", side_effect=_publish):
        result = run_ga(payload)
    result["metrics"].pop("elapsed_seconds")

    assert "locks_enforced" not in result
    assert len(result["candidate"]["assignments"]) == 2
    assert result["metrics"]["required_class_count"] == 2
    assert result["metrics"]["assignment_completeness"] == 1.0


def test_ga_handler_routes_block_when_block_context_present():
    problem = make_ga_problem(include_unstaffable=False)
    selected_classes = (problem.classes[0],)
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000006"),
        selected_block=BlockSectionDTO(
            block_id=UUID("00000000-0000-0000-0000-000000000006"),
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=selected_classes,
        locked_assignments=(),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )
    config = GAConfig(
        population_size=4,
        max_generations=2,
        tournament_size=2,
        elite_count=1,
        random_seed=17,
    )
    payload = encode_block_ga_payload(context, config)

    result = run_ga(payload)
    result["metrics"].pop("elapsed_seconds")

    assert result["locks_enforced"] is True
    assert len(result["candidate"]["assignments"]) == 1
    assert result["metrics"]["required_class_count"] == 1
    assert result["metrics"]["assignment_completeness"] == 1.0


def test_ga_block_handler_refuses_legacy_locks_without_offering_identity():
    problem = make_ga_problem(include_unstaffable=False)
    block_id = UUID("00000000-0000-0000-0000-000000000006")
    # Six accepted three-unit classes: a part-time teacher's full 18 units.
    locks = tuple(
        LockedAssignment(
            class_id=UUID(f"00000000-0000-0000-0000-0000000005{index:02d}"),
            faculty_id=FACULTY_ID,
            time_slot_id=TIME_SLOT_ID,
            room_code=problem.rooms[0].room_code,
            meeting_days=days,
            block_id=UUID("00000000-0000-0000-0000-000000000007"),
            offering_id=UUID(f"00000000-0000-0000-0000-0000000006{index:02d}"),
            units=3,
        )
        for index, days in enumerate(("M", "T", "W", "TH", "F", "S"))
    )
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=block_id,
        selected_block=BlockSectionDTO(
            block_id=block_id,
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=(problem.classes[0],),
        locked_assignments=locks,
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )
    payload = encode_block_ga_payload(
        context,
        GAConfig(
            population_size=4,
            max_generations=1,
            tournament_size=2,
            elite_count=1,
            random_seed=17,
        ),
    )
    # A context queued before CHR-SCH-012 carries neither field.
    for item in payload["block_context"]["context"]["locked_assignments"]:
        del item["offering_id"], item["units"]

    with pytest.raises(ValueError, match="resubmit"):
        run_ga(payload)


def test_ga_block_handler_rebuilds_offering_block_ids_from_selected_classes(monkeypatch):
    problem = make_ga_problem(include_unstaffable=False)
    selected_classes = (problem.classes[0],)
    # Give context a term-wide offering_block_ids mapping that has extra classes
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000006"),
        selected_block=BlockSectionDTO(
            block_id=UUID("00000000-0000-0000-0000-000000000006"),
            term_id=TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=selected_classes,
        locked_assignments=(),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )
    config = GAConfig(
        population_size=4,
        max_generations=1,
        tournament_size=2,
        elite_count=1,
        random_seed=17,
    )
    payload = encode_block_ga_payload(context, config)

    captured_problems = []
    from app.services.ga.engine import GAEngine
    original_init = GAEngine.__init__

    def spy_init(self, problem, config, *args, **kwargs):
        captured_problems.append(problem)
        original_init(self, problem, config, *args, **kwargs)

    monkeypatch.setattr(GAEngine, "__init__", spy_init)
    run_ga(payload)

    assert len(captured_problems) == 1
    scoped_problem = captured_problems[0]
    assert scoped_problem.classes == selected_classes
    # Rebuilt offering_block_ids should only contain the offering IDs of selected_classes
    expected_offering_ids = {c.offering_id for c in selected_classes}
    assert set(scoped_problem.offering_block_ids.keys()) == expected_offering_ids


def test_ga_handler_persists_schedule_with_job_id():
    """Successful GA child result is persisted as a schedule with schedule_id."""
    job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    mock_session = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__ = MagicMock(return_value=mock_session)
    mock_context.__exit__ = MagicMock(return_value=False)

    with patch("app.db.session.SessionLocal", return_value=mock_context), \
         patch("app.services.schedules.find_published_schedule", return_value=None), \
         patch("app.services.schedules.persist_candidate", side_effect=_publish) as persist:
        result = run_ga(_ga_payload(job_id=job_id))

    persisted = persist.call_args.args[1]
    assert result["candidate"]["schedule_id"] == str(persisted.schedule_id)
    assert result["metrics"]["assignment_completeness"] == 1.0
    assert persisted.job_id == job_id
    assert persist.call_args.kwargs == {
        "claim_token": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"),
        "result": {**result, "published_attempt_processing_seconds": ANY},  # CHR-192
    }


@pytest.mark.parametrize("missing", ["job_id", "claim_token"])
def test_ga_handler_refuses_publication_without_claim_identity(missing):
    payload = _ga_payload()
    del payload[missing]
    with patch("app.services.schedules.persist_candidate") as persist:
        with pytest.raises(ValueError, match="GA publication requires a live worker claim"):
            run_ga(payload)
    persist.assert_not_called()


def test_ga_handler_raises_when_persistence_fails():
    """Handler lets persistence errors propagate instead of swallowing them."""
    mock_session = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__ = MagicMock(return_value=mock_session)
    mock_context.__exit__ = MagicMock(return_value=False)

    with patch("app.db.session.SessionLocal", return_value=mock_context), \
         patch("app.services.schedules.find_published_schedule", return_value=None), \
         patch("app.services.schedules.persist_candidate", side_effect=RuntimeError("DB down")):
        with pytest.raises(RuntimeError, match="DB down"):
            run_ga(_ga_payload())
