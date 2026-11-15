"""Short-transaction execution-job repository for CHR-31 / CHR-WORKER-001."""

import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import (
    String,
    and_,
    cast,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Session

from app.models import EvaluationBatch, ExecutionJob, JobStatus, JobType, RuleSet, Schedule

FINISHED_STATUSES = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMED_OUT)


@dataclass(frozen=True, slots=True)
class ExecutionJobRecord:
    job_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    requested_by: uuid.UUID
    request: dict[str, Any]
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    timeout_seconds: int
    attempt_count: int
    max_attempts: int
    claimed_by: str | None
    claim_token: uuid.UUID | None
    lease_expires_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobHistoryRow:
    """CHR-174: a history row without results, claims or the queued inputs.

    ``request`` holds only the payload paths the history view reads (codec
    version, frozen guidance and the term/Block identity), never the problem
    or historical records themselves.
    """

    job_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    request: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class StaleRecoveryResult:
    requeued: int
    failed: int


class JobRepository:
    """Persist jobs without retaining ORM objects or owning the transaction."""

    _ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(self, session: Session):
        self._session = session

    def enqueue(
        self,
        *,
        job_type: JobType | str,
        requested_by: uuid.UUID,
        request: Mapping[str, Any],
        timeout_seconds: int,
        max_attempts: int = 1,
    ) -> ExecutionJobRecord:
        job_type = JobType(job_type)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        job = ExecutionJob(
            job_type=job_type.value,
            status=JobStatus.QUEUED.value,
            requested_by=requested_by,
            request=deepcopy(dict(request)),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )
        self._session.add(job)
        self._session.flush()
        self._session.refresh(job)
        return self._record(job)

    def get(self, job_id: uuid.UUID) -> ExecutionJobRecord | None:
        job = self._session.get(ExecutionJob, job_id)
        return None if job is None else self._record(job)

    def get_for_user(
        self, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExecutionJobRecord | None:
        job = self._session.scalars(
            select(ExecutionJob).where(
                ExecutionJob.job_id == job_id,
                ExecutionJob.requested_by == user_id,
            )
        ).one_or_none()
        return None if job is None else self._record(job)

    def list_history(
        self,
        *,
        requested_by: uuid.UUID | None = None,
        job_types: Sequence[JobType] | None = None,
        status: JobStatus | None = None,
        term_id: uuid.UUID | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobHistoryRow], int]:
        """CHR-174 / CHR-SCH-014: newest-first job history page and its total."""
        filters = []
        if requested_by is not None:
            filters.append(ExecutionJob.requested_by == requested_by)
        if job_types is not None:
            filters.append(ExecutionJob.job_type.in_([item.value for item in job_types]))
        if status is not None:
            filters.append(ExecutionJob.status == status.value)
        if term_id is not None:
            # Term-wide and per-Block GA payloads keep the term in different places.
            filters.append(
                func.coalesce(
                    ExecutionJob.request["problem"]["term"]["term_id"].astext,
                    ExecutionJob.request["block_context"]["context"]["term"]["term_id"].astext,
                )
                == str(term_id)
            )
        search = (search or "").strip()
        if search:
            # Literal substring match: LIKE wildcards in the search are escaped.
            filters.append(
                or_(
                    cast(ExecutionJob.job_id, String).icontains(search, autoescape=True),
                    ExecutionJob.error_code.icontains(search, autoescape=True),
                )
            )
        total = self._session.scalar(
            select(func.count()).select_from(ExecutionJob).where(*filters)
        )
        # JSON paths keep big problem/result payloads out of the page query;
        # jsonb `->` on a non-object yields NULL, so odd rows cannot break it.
        request = ExecutionJob.request
        context = request["block_context"]["context"]
        rows = self._session.execute(
            select(
                ExecutionJob.job_id,
                ExecutionJob.job_type,
                ExecutionJob.status,
                ExecutionJob.error_code,
                ExecutionJob.error_message,
                ExecutionJob.created_at,
                ExecutionJob.started_at,
                ExecutionJob.finished_at,
                request["version"].label("version"),
                request["historical_guidance"].label("historical_guidance"),
                request["problem"]["term"].label("problem_term"),
                context["term"].label("context_term"),
                context["selected_block_id"].label("selected_block_id"),
                context["selected_block"].label("selected_block"),
            )
            .where(*filters)
            .order_by(ExecutionJob.created_at.desc(), ExecutionJob.job_id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [self._history_row(row) for row in rows], total or 0

    def iterations(self, job_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Each job's 1-based run number among jobs of the same kind, term,
        Block and frozen rule set, oldest first. Deleted jobs drop out, so
        later runs renumber.
        """
        if not job_ids:
            return {}
        request = ExecutionJob.request
        # ponytail: windows over every GA/evaluation job per call; store the
        # number at submission if job history grows past a few thousand rows.
        numbered = (
            select(
                ExecutionJob.job_id,
                func.row_number()
                .over(
                    partition_by=(
                        ExecutionJob.job_type,
                        func.coalesce(
                            request["problem"]["term"]["term_id"].astext,
                            request["block_context"]["context"]["term"]["term_id"].astext,
                        ),
                        request["block_context"]["context"]["selected_block_id"].astext,
                        request["historical_guidance"]["rule_set_id"].astext,
                    ),
                    order_by=(ExecutionJob.created_at, ExecutionJob.job_id),
                )
                .label("iteration"),
            )
            .where(
                ExecutionJob.job_type.in_(
                    [JobType.GA.value, JobType.EVALUATION.value]
                )
            )
            .subquery()
        )
        rows = self._session.execute(
            select(numbered.c.job_id, numbered.c.iteration).where(
                numbered.c.job_id.in_(list(job_ids))
            )
        ).all()
        return {row.job_id: row.iteration for row in rows}

    @staticmethod
    def _history_row(row: Any) -> JobHistoryRow:
        slim: dict[str, Any] = {"version": row.version}
        if row.historical_guidance is not None:
            slim["historical_guidance"] = row.historical_guidance
        if row.problem_term is not None:
            slim["problem"] = {"term": row.problem_term}
        if row.context_term is not None or row.selected_block_id is not None:
            slim["block_context"] = {
                "context": {
                    "term": row.context_term,
                    "selected_block_id": row.selected_block_id,
                    # Only the cohort tuple the display ID reads.
                    "selected_block": {
                        key: row.selected_block.get(key)
                        for key in ("program_code", "year_level", "section_letter")
                    }
                    if isinstance(row.selected_block, dict)
                    else None,
                }
            }
        return JobHistoryRow(
            job_id=row.job_id,
            job_type=JobType(row.job_type),
            status=JobStatus(row.status),
            request=slim,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    def delete_finished(
        self,
        job_ids: Sequence[uuid.UUID],
        *,
        requested_by: uuid.UUID | None = None,
        job_types: Sequence[JobType] | None = None,
    ) -> dict[uuid.UUID, str]:
        """Delete finished jobs nothing references; report why the rest stayed.

        Returns ``{job_id: outcome}`` where outcome is ``deleted``,
        ``not_found`` (missing or outside the caller's scope), ``active``
        (queued or running, so a worker may still own it) or ``in_use`` (a
        schedule, rule set or evaluation batch still points at it).
        """
        ids = list(dict.fromkeys(job_ids))
        scope = [ExecutionJob.job_id.in_(ids)]
        if requested_by is not None:
            scope.append(ExecutionJob.requested_by == requested_by)
        if job_types is not None:
            scope.append(ExecutionJob.job_type.in_([item.value for item in job_types]))
        # Lock first so a worker cannot claim a queued row between check and delete.
        rows = self._session.execute(
            select(ExecutionJob.job_id, ExecutionJob.status).where(*scope).with_for_update()
        ).all()
        outcome = {job_id: "not_found" for job_id in ids}
        finished = []
        for row in rows:
            if JobStatus(row.status) in FINISHED_STATUSES:
                finished.append(row.job_id)
            else:
                outcome[row.job_id] = "active"
        referenced = set()
        for model in (Schedule, RuleSet, EvaluationBatch):
            referenced.update(
                self._session.scalars(
                    select(model.job_id).where(model.job_id.in_(finished))
                ).all()
            )
        deletable = [job_id for job_id in finished if job_id not in referenced]
        for job_id in referenced:
            outcome[job_id] = "in_use"
        if deletable:
            self._session.execute(
                delete(ExecutionJob).where(ExecutionJob.job_id.in_(deletable))
            )
            outcome.update({job_id: "deleted" for job_id in deletable})
        return outcome

    def claim_next(
        self,
        *,
        claimed_by: str,
        lease_seconds: int,
        job_types: Sequence[JobType | str] | None = None,
    ) -> ExecutionJobRecord | None:
        worker = claimed_by.strip()
        if not worker:
            raise ValueError("claimed_by must not be blank")
        self._require_positive_lease(lease_seconds)
        filters = [
            ExecutionJob.status == JobStatus.QUEUED.value,
            ExecutionJob.attempt_count < ExecutionJob.max_attempts,
        ]
        if job_types is not None:
            values = [JobType(item).value for item in job_types]
            if not values:
                return None
            filters.append(ExecutionJob.job_type.in_(values))

        job_id = self._session.scalar(
            select(ExecutionJob.job_id)
            .where(*filters)
            .order_by(ExecutionJob.created_at, ExecutionJob.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job_id is None:
            return None

        now = func.clock_timestamp()
        token = uuid.uuid4()
        statement = (
            update(ExecutionJob)
            .where(ExecutionJob.job_id == job_id)
            .values(
                status=JobStatus.RUNNING.value,
                claimed_by=worker,
                claim_token=token,
                lease_expires_at=self._lease_deadline(lease_seconds),
                attempt_count=ExecutionJob.attempt_count + 1,
                started_at=func.coalesce(ExecutionJob.started_at, now),
                updated_at=now,
            )
            .returning(ExecutionJob)
        )
        return self._record(self._session.scalars(statement).one())

    def heartbeat(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        lease_seconds: int,
        progress: Mapping[str, Any] | None = None,
    ) -> ExecutionJobRecord | None:
        self._require_positive_lease(lease_seconds)
        values: dict[str, Any] = {
            "lease_expires_at": self._lease_deadline(lease_seconds),
            "updated_at": func.clock_timestamp(),
        }
        if progress is not None:
            values["progress"] = deepcopy(dict(progress))
        return self._fenced_update(job_id, claim_token, values)

    def lock_live_claim(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        job_type: JobType | None = None,
    ) -> bool:
        """CHR-126 / CHR-ARM-006: fence artifact writes in the caller's transaction."""
        locked_id = self._session.scalar(
            select(ExecutionJob.job_id)
            .where(ExecutionJob.job_id == job_id)
            .with_for_update()
        )
        if locked_id is None:
            return False
        # Check the database clock after any lock wait, not its pre-wait value.
        return self._session.scalar(
            select(ExecutionJob.job_id).where(
                ExecutionJob.job_id == job_id,
                *([ExecutionJob.job_type == job_type.value] if job_type is not None else []),
                ExecutionJob.status == JobStatus.RUNNING.value,
                ExecutionJob.claim_token == claim_token,
                ExecutionJob.lease_expires_at > func.clock_timestamp(),
            )
        ) is not None

    def complete(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        result: Mapping[str, Any],
    ) -> ExecutionJobRecord | None:
        now = func.clock_timestamp()
        return self._fenced_update(
            job_id,
            claim_token,
            {
                "status": JobStatus.SUCCEEDED.value,
                "result": deepcopy(dict(result)),
                "finished_at": now,
                "updated_at": now,
                **self._clear_claim(),
            },
        )

    def fail(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ExecutionJobRecord | None:
        return self._finish_error(
            job_id,
            claim_token,
            JobStatus.FAILED,
            error_code,
            error_message,
        )

    def timeout(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        error_code: str = "execution_timed_out",
        error_message: str = "Execution exceeded its configured timeout.",
    ) -> ExecutionJobRecord | None:
        return self._finish_error(
            job_id,
            claim_token,
            JobStatus.TIMED_OUT,
            error_code,
            error_message,
        )

    def recover_stale(self, *, limit: int = 100) -> StaleRecoveryResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = func.clock_timestamp()
        stale_ids = self._session.scalars(
            select(ExecutionJob.job_id)
            .where(
                ExecutionJob.status == JobStatus.RUNNING.value,
                ExecutionJob.lease_expires_at <= now,
            )
            .order_by(ExecutionJob.lease_expires_at, ExecutionJob.job_id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        if not stale_ids:
            return StaleRecoveryResult(requeued=0, failed=0)

        retryable = and_(
            ExecutionJob.job_id.in_(stale_ids),
            ExecutionJob.attempt_count < ExecutionJob.max_attempts,
        )
        requeued = self._session.execute(
            update(ExecutionJob)
            .where(retryable)
            .values(
                status=JobStatus.QUEUED.value,
                updated_at=now,
                **self._clear_claim(),
            )
        ).rowcount
        failed = self._session.execute(
            update(ExecutionJob)
            .where(
                ExecutionJob.job_id.in_(stale_ids),
                ExecutionJob.attempt_count >= ExecutionJob.max_attempts,
            )
            .values(
                status=JobStatus.FAILED.value,
                error_code="worker_lease_exhausted",
                error_message="Worker lease expired and no attempts remain.",
                finished_at=now,
                updated_at=now,
                **self._clear_claim(),
            )
        ).rowcount
        return StaleRecoveryResult(requeued=requeued, failed=failed)

    def _finish_error(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        status: JobStatus,
        error_code: str,
        error_message: str,
    ) -> ExecutionJobRecord | None:
        code, message = self._validate_error(error_code, error_message)
        now = func.clock_timestamp()
        return self._fenced_update(
            job_id,
            claim_token,
            {
                "status": status.value,
                "error_code": code,
                "error_message": message,
                "finished_at": now,
                "updated_at": now,
                **self._clear_claim(),
            },
        )

    def _fenced_update(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        values: Mapping[str, Any],
    ) -> ExecutionJobRecord | None:
        if not self.lock_live_claim(job_id, claim_token):
            return None
        now = func.clock_timestamp()
        statement = (
            update(ExecutionJob)
            .where(
                ExecutionJob.job_id == job_id,
                ExecutionJob.status == JobStatus.RUNNING.value,
                ExecutionJob.claim_token == claim_token,
                ExecutionJob.lease_expires_at > now,
            )
            .values(**values)
            .returning(ExecutionJob)
        )
        job = self._session.scalars(statement).one_or_none()
        return None if job is None else self._record(job)

    @staticmethod
    def _lease_deadline(lease_seconds: int):
        return func.clock_timestamp() + literal(lease_seconds) * literal_column(
            "INTERVAL '1 second'"
        )

    @staticmethod
    def _clear_claim() -> dict[str, None]:
        return {"claimed_by": None, "claim_token": None, "lease_expires_at": None}

    @staticmethod
    def _require_positive_lease(lease_seconds: int) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

    @classmethod
    def _validate_error(cls, code: str, message: str) -> tuple[str, str]:
        code = code.strip()
        message = message.strip()
        if cls._ERROR_CODE.fullmatch(code) is None:
            raise ValueError("error_code must be a safe lowercase identifier")
        if not message:
            raise ValueError("error_message must not be blank")
        if len(message) > 1000:
            raise ValueError("error_message must not exceed 1000 characters")
        return code, message

    @staticmethod
    def _record(job: ExecutionJob) -> ExecutionJobRecord:
        return ExecutionJobRecord(
            job_id=job.job_id,
            job_type=JobType(job.job_type),
            status=JobStatus(job.status),
            requested_by=job.requested_by,
            request=deepcopy(job.request),
            progress=deepcopy(job.progress),
            result=deepcopy(job.result),
            timeout_seconds=job.timeout_seconds,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            claimed_by=job.claimed_by,
            claim_token=job.claim_token,
            lease_expires_at=job.lease_expires_at,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


__all__ = ["ExecutionJobRecord", "JobHistoryRow", "JobRepository", "StaleRecoveryResult"]
