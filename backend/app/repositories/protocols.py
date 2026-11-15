from typing import Protocol
from uuid import UUID

from app.domain import BlockGenerationContext, SchedulingProblem


class SchedulingProblemNotFoundError(LookupError):
    def __init__(self, term_id: UUID):
        super().__init__(f"Scheduling term not found: {term_id}")
        self.term_id = term_id


class BlockGenerationContextNotFoundError(LookupError):
    def __init__(self, term_id: UUID, block_id: UUID):
        super().__init__(
            f"Block generation context not found: term={term_id}, block={block_id}"
        )
        self.term_id = term_id
        self.block_id = block_id


class BaseScheduleNotFoundError(LookupError):
    def __init__(self, term_id: UUID, schedule_id: UUID):
        super().__init__(
            f"Base schedule not found for term: term={term_id}, schedule={schedule_id}"
        )
        self.term_id = term_id
        self.schedule_id = schedule_id


class SchedulingProblemProvider(Protocol):
    def get_problem(self, term_id: UUID) -> SchedulingProblem: ...


class BlockGenerationContextProvider(Protocol):
    def get_block_generation_context(
        self,
        term_id: UUID,
        block_id: UUID,
        base_schedule_id: UUID | None = None,
    ) -> BlockGenerationContext: ...
