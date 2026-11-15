from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ConflictCode(StrEnum):
    FACULTY_OVERLAP = "FACULTY_OVERLAP"
    ROOM_OVERLAP = "ROOM_OVERLAP"
    COHORT_OVERLAP = "COHORT_OVERLAP"
    FACULTY_UNAVAILABLE = "FACULTY_UNAVAILABLE"
    FACULTY_WORKLOAD_EXCEEDED = "FACULTY_WORKLOAD_EXCEEDED"
    ROOM_CAPACITY_EXCEEDED = "ROOM_CAPACITY_EXCEEDED"
    ROOM_TYPE_MISMATCH = "ROOM_TYPE_MISMATCH"
    SUBJECT_DURATION_MISMATCH = "SUBJECT_DURATION_MISMATCH"
    SHARED_OFFERING_MISMATCH = "SHARED_OFFERING_MISMATCH"
    OUTSIDE_TEACHING_CALENDAR = "OUTSIDE_TEACHING_CALENDAR"
    PAIRED_LAB_MISMATCH = "PAIRED_LAB_MISMATCH"
    INVALID_MEETING_PATTERN = "INVALID_MEETING_PATTERN"
    # A teacher placed on a subject they are barred from: marked non-preferred,
    # or a laboratory subject for a lecture-only teacher.
    FACULTY_INELIGIBLE = "FACULTY_INELIGIBLE"
    # A class taught by a member since marked as not currently teaching. Only
    # schedules still being built report it; finalized ones keep past teachers.
    FACULTY_INACTIVE = "FACULTY_INACTIVE"
    FACULTY_CONSECUTIVE_EXCEEDED = "FACULTY_CONSECUTIVE_EXCEEDED"


@dataclass(frozen=True, slots=True)
class ConstraintConflict:
    code: ConflictCode
    resource_key: tuple[str, ...]
    class_ids: tuple[UUID, ...]
    time_slot_ids: tuple[UUID, ...]
    message: str


@dataclass(frozen=True, slots=True)
class ConstraintReport:
    conflicts: tuple[ConstraintConflict, ...]

    @property
    def hard_conflict_count(self) -> int:
        return len(self.conflicts)

    @property
    def is_valid(self) -> bool:
        return not self.conflicts


@dataclass(frozen=True, slots=True)
class ScheduleValidationResult:
    required_class_count: int
    assigned_class_count: int
    unassigned_class_ids: tuple[UUID, ...]
    constraint_report: ConstraintReport

    @property
    def is_complete(self) -> bool:
        return (
            self.required_class_count > 0
            and self.assigned_class_count == self.required_class_count
        )

    @property
    def hard_conflict_count(self) -> int:
        return self.constraint_report.hard_conflict_count

    @property
    def is_export_eligible(self) -> bool:
        return self.is_complete and self.hard_conflict_count == 0
