"""CHR-77 / CHR-SCH-005 job submission service and route tests."""

from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    require_committee_member,
)
from app.api.routes_jobs import get_job_submission_service
from app.domain import BlockGenerationContext
from app.main import create_app
from app.models import AppUser, JobStatus, JobType, UserRole
from app.repositories.protocols import (
    BaseScheduleNotFoundError,
    BlockGenerationContextNotFoundError,
    SchedulingProblemNotFoundError,
)
from app.schemas.jobs import GAConfigRequest, SubmitGAJobRequest
from app.services.ga.config import GAConfig
from app.services.job_submission import (
    GAGuidanceError,
    GATermNotReadyError,
    JobSubmissionService,
)
from app.tests.support.scheduling import BLOCK_ID, SCHEDULE_ID, make_scheduling_problem


@pytest.mark.parametrize("per_block", [False, True])
def test_submit_ga_refuses_unready_inputs_before_encoding_or_writes(per_block):
    """CHR-124 / CHR-BLK-007: the service gate does not depend on the UI."""
    problem = replace(make_scheduling_problem(), rooms=())
    session = MagicMock()
    session.scalar.return_value = None
    repository = MagicMock()
    ga_encoder = MagicMock(return_value={})
    block_encoder = MagicMock(return_value={})
    service = JobSubmissionService(
        session, repository, ga_encoder, MagicMock(), block_encoder,
        max_attempts=1, ga_timeout_seconds=60, arm_timeout_seconds=60,
    )
    request = SubmitGAJobRequest(
        term_id=problem.term.term_id,
        block_id=BLOCK_ID if per_block else None,
        base_schedule_id=SCHEDULE_ID if per_block else None,
    )
    with (
        patch("app.services.job_submission.SQLAlchemySchedulingProblemProvider") as provider,
        patch("app.services.job_submission.SQLAlchemyBlockGenerationContextProvider"),
        patch("app.services.job_submission.DatabaseRuleProvider") as guidance,
    ):
        provider.return_value.get_problem.return_value = problem
        with pytest.raises(GATermNotReadyError, match="not ready") as error:
            service.submit_ga(request, uuid4())
    assert [item.code for item in error.value.report.blockers] == ["NO_ROOMS"]
    ga_encoder.assert_not_called()
    block_encoder.assert_not_called()
    guidance.assert_not_called()
    repository.enqueue.assert_not_called()
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_submit_ga_without_block_id_enqueues_term_wide_problem():
    session = MagicMock()
    session.scalar.return_value = None
    repository = MagicMock()
    ga_encoder = MagicMock(return_value={"kind": "term_ga"})
    arm_encoder = MagicMock()
    block_ga_encoder = MagicMock()

    service = JobSubmissionService(
        session=session,
        repository=repository,
        ga_payload_encoder=ga_encoder,
        arm_payload_encoder=arm_encoder,
        block_ga_payload_encoder=block_ga_encoder,
        max_attempts=3,
        ga_timeout_seconds=300,
        arm_timeout_seconds=600,
    )

    term_id = make_scheduling_problem().term.term_id
    user_id = uuid4()
    request = SubmitGAJobRequest(term_id=term_id)

    mock_problem = make_scheduling_problem()
    with patch(
        "app.services.job_submission.SQLAlchemySchedulingProblemProvider"
    ) as mock_provider_cls:
        mock_provider = mock_provider_cls.return_value
        mock_provider.get_problem.return_value = mock_problem

        service.submit_ga(request, user_id)

        mock_provider_cls.assert_called_once_with(session)
        mock_provider.get_problem.assert_called_once_with(term_id)
        ga_encoder.assert_called_once()
        block_ga_encoder.assert_not_called()
        repository.enqueue.assert_called_once_with(
            job_type="ga",
            requested_by=user_id,
            request={
                "kind": "term_ga",
                "historical_guidance": {
                    "mode": "unguided",
                    "scoring_policy": "equal_applicable_streams_v1",
                    "rule_set_id": None,
                    "rule_set_version": None,
                    "rules": [],
                },
            },
            timeout_seconds=300,
            max_attempts=3,
        )
        session.commit.assert_called_once()


def test_submit_ga_with_block_id_snapshots_context_and_enqueues():
    session = MagicMock()
    session.scalar.return_value = None
    repository = MagicMock()
    ga_encoder = MagicMock()
    arm_encoder = MagicMock()
    block_ga_encoder = MagicMock(return_value={"kind": "block_ga"})

    service = JobSubmissionService(
        session=session,
        repository=repository,
        ga_payload_encoder=ga_encoder,
        arm_payload_encoder=arm_encoder,
        block_ga_payload_encoder=block_ga_encoder,
        max_attempts=2,
        ga_timeout_seconds=120,
        arm_timeout_seconds=300,
    )

    term_id = make_scheduling_problem().term.term_id
    block_id = uuid4()
    base_schedule_id = uuid4()
    user_id = uuid4()
    request = SubmitGAJobRequest(
        term_id=term_id,
        block_id=block_id,
        base_schedule_id=base_schedule_id,
    )

    mock_context = MagicMock(spec=BlockGenerationContext)
    with (
        patch("app.services.job_submission.SQLAlchemyBlockGenerationContextProvider") as mock_provider_cls,
        patch("app.services.job_submission.SQLAlchemySchedulingProblemProvider") as full_provider,
    ):
        full_provider.return_value.get_problem.return_value = make_scheduling_problem()
        mock_provider = mock_provider_cls.return_value
        mock_provider.get_block_generation_context.return_value = mock_context

        service.submit_ga(request, user_id)

        mock_provider_cls.assert_called_once_with(session)
        mock_provider.get_block_generation_context.assert_called_once_with(
            term_id,
            block_id,
            base_schedule_id=base_schedule_id,
        )
        block_ga_encoder.assert_called_once()
        ga_encoder.assert_not_called()
        repository.enqueue.assert_called_once_with(
            job_type="ga",
            requested_by=user_id,
            request={
                "kind": "block_ga",
                "historical_guidance": {
                    "mode": "unguided",
                    "scoring_policy": "equal_applicable_streams_v1",
                    "rule_set_id": None,
                    "rule_set_version": None,
                    "rules": [],
                },
            },
            timeout_seconds=120,
            max_attempts=2,
        )
        session.commit.assert_called_once()


def test_submit_ga_server_config_policy():
    session = MagicMock()
    session.scalar.return_value = None
    repository = MagicMock()
    ga_encoder = MagicMock(return_value={})
    block_ga_encoder = MagicMock()

    service = JobSubmissionService(
        session=session,
        repository=repository,
        ga_payload_encoder=ga_encoder,
        arm_payload_encoder=MagicMock(),
        block_ga_payload_encoder=block_ga_encoder,
        max_attempts=1,
        ga_timeout_seconds=60,
        arm_timeout_seconds=60,
    )

    custom_config = GAConfigRequest(population_size=10, max_generations=5)
    request = SubmitGAJobRequest(term_id=uuid4(), config=custom_config)

    saved_config = GAConfig(population_size=30, max_generations=40)
    with (
        patch("app.services.job_submission.SQLAlchemySchedulingProblemProvider") as provider,
        patch(
            "app.services.job_submission.active_ga_config", return_value=saved_config
        ) as active_config,
    ):
        provider.return_value.get_problem.return_value = make_scheduling_problem()
        # non-admin / use_server_config=True uses the active saved configuration
        service.submit_ga(request, uuid4(), use_server_config=True)
        active_config.assert_called_once_with(session)
        assert ga_encoder.call_args[0][1] is saved_config

        # admin without an override also uses the active saved configuration
        service.submit_ga(SubmitGAJobRequest(term_id=uuid4()), uuid4())
        assert ga_encoder.call_args[0][1] is saved_config

        # admin / use_server_config=False should use requested config
        service.submit_ga(request, uuid4(), use_server_config=False)
        passed_config_custom = ga_encoder.call_args[0][1]
        assert passed_config_custom.population_size == 10
        assert passed_config_custom.max_generations == 5


def test_submit_ga_refuses_a_saved_sunday_configuration_before_running():
    """CHR-168: a pre-CHR-125 saved config may still list Sunday."""
    session = MagicMock()
    repository = MagicMock()
    ga_encoder = MagicMock(return_value={})
    service = JobSubmissionService(
        session=session,
        repository=repository,
        ga_payload_encoder=ga_encoder,
        arm_payload_encoder=MagicMock(),
        block_ga_payload_encoder=MagicMock(),
        max_attempts=1,
        ga_timeout_seconds=60,
        arm_timeout_seconds=60,
    )

    with (
        patch(
            "app.services.job_submission.active_ga_config",
            return_value=GAConfig(meeting_day_patterns=("MWF", "Su")),
        ),
        pytest.raises(GAGuidanceError) as error,
    ):
        service.submit_ga(SubmitGAJobRequest(term_id=uuid4()), uuid4())

    assert error.value.code == "INVALID_GA_CONFIGURATION"
    ga_encoder.assert_not_called()
    repository.create.assert_not_called()


def test_submit_ga_refuses_repeated_guidance_replacement_before_enqueue():
    session = MagicMock()
    # The first read is the active GA configuration; none saved means defaults.
    session.scalar.side_effect = [None] + [None, uuid4()] * 3
    repository = MagicMock()
    service = JobSubmissionService(
        session=session,
        repository=repository,
        ga_payload_encoder=MagicMock(return_value={}),
        arm_payload_encoder=MagicMock(),
        block_ga_payload_encoder=MagicMock(),
        max_attempts=1,
        ga_timeout_seconds=60,
        arm_timeout_seconds=60,
    )

    with (
        patch("app.services.job_submission.SQLAlchemySchedulingProblemProvider") as provider,
        pytest.raises(GAGuidanceError) as error,
    ):
        provider.return_value.get_problem.return_value = make_scheduling_problem()
        service.submit_ga(SubmitGAJobRequest(term_id=uuid4()), uuid4())

    assert error.value.code == "GUIDANCE_CHANGED"
    repository.enqueue.assert_not_called()
    session.commit.assert_not_called()


@pytest.fixture
def api_test_setup():
    app = create_app()
    mock_service = MagicMock(spec=JobSubmissionService)
    test_user = AppUser(
        user_id=uuid4(),
        username="scheduler_user",
        password_hash="hash",
        role=UserRole.SCHEDULING_COMMITTEE,
        is_active=True,
    )

    app.dependency_overrides[require_committee_member] = lambda: test_user
    app.dependency_overrides[get_job_submission_service] = lambda: mock_service

    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), raise_server_exceptions=False)
    return client, mock_service


def test_route_ga_block_not_found_returns_404(api_test_setup):
    client, mock_service = api_test_setup
    term_id = uuid4()
    block_id = uuid4()
    mock_service.submit_ga.side_effect = BlockGenerationContextNotFoundError(
        term_id, block_id
    )

    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(term_id),
            "block_id": str(block_id),
            "base_schedule_id": str(uuid4()),
        },
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "The requested block was not found."


@pytest.mark.parametrize("code", ["RULE_SET_INVALID", "GUIDANCE_CHANGED"])
def test_route_reports_known_guidance_refusals_without_exposing_details(
    api_test_setup, code
):
    client, service = api_test_setup
    service.submit_ga.side_effect = GAGuidanceError(
        code, "Historical guidance requires administrator review."
    )
    response = client.post("/api/v1/jobs/ga", json={"term_id": str(uuid4())})
    assert response.status_code == 409
    assert response.json()["code"] == code
    assert (
        response.json()["message"]
        == "Historical guidance requires administrator review."
    )


def test_route_ga_base_schedule_not_found_returns_404(api_test_setup):
    client, mock_service = api_test_setup
    term_id = uuid4()
    schedule_id = uuid4()
    mock_service.submit_ga.side_effect = BaseScheduleNotFoundError(term_id, schedule_id)

    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(term_id),
            "block_id": str(uuid4()),
            "base_schedule_id": str(schedule_id),
        },
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "The referenced base schedule was not found."


def test_route_ga_term_not_found_returns_404(api_test_setup):
    client, mock_service = api_test_setup
    term_id = uuid4()
    mock_service.submit_ga.side_effect = SchedulingProblemNotFoundError(term_id)

    response = client.post(
        "/api/v1/jobs/ga",
        json={"term_id": str(term_id)},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "The requested term was not found."


def test_route_ga_missing_base_schedule_returns_422(api_test_setup):
    client, _ = api_test_setup

    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(uuid4()),
            "block_id": str(uuid4()),
        },
    )

    assert response.status_code == 422


def test_route_ga_successful_block_submission_returns_202(api_test_setup):
    client, mock_service = api_test_setup
    job_id = uuid4()
    mock_job = MagicMock()
    mock_job.job_id = job_id
    mock_job.job_type = JobType.GA
    mock_job.status = JobStatus.QUEUED
    mock_service.submit_ga.return_value = mock_job

    response = client.post(
        "/api/v1/jobs/ga",
        json={
            "term_id": str(uuid4()),
            "block_id": str(uuid4()),
            "base_schedule_id": str(uuid4()),
        },
    )

    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/jobs/{job_id}"
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["kind"] == "ga"
    assert body["status"] == "queued"
