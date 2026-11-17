"""SQLAlchemy models.

Every model must be re-exported here — Alembic autogenerate only sees what is
registered on Base.metadata when alembic/env.py imports this package.
"""

from app.models.app_user import AppUser, UserRole
from app.models.audit_event import AuditEvent
from app.models.auth_session import AuthSession
from app.models.block_section import BlockSection
from app.models.block_section_offering import BlockSectionOffering
from app.models.building import Building
from app.models.course_program import CourseProgram
from app.models.curriculum import Curriculum, CurriculumRequirement
from app.models.execution_job import ExecutionJob, JobStatus, JobType
from app.models.extracted_rule import ExtractedRule
from app.models.evaluation import EvaluationBatch, EvaluationRun
from app.models.faculty import Faculty, faculty_preferred_subject
from app.models.historical_import_batch import (
    HistoricalImportBatch,
    HistoricalImportRejection,
)
from app.models.ga_configuration import GAConfiguration
from app.models.hard_constraint_configuration import HardConstraintConfiguration
from app.models.historical_snapshot import HistoricalRecord, HistoricalSnapshot
from app.models.room import Room
from app.models.rule_set import (
    RuleSet,
    RuleSetThreshold,
    rule_set_source_snapshot,
    rule_set_term,
)
from app.models.rule_set_faculty_metric import RuleSetFacultyMetric
from app.models.schedule import Schedule, ScheduleType
from app.models.schedule_record import ScheduleRecord
from app.models.schedule_unlocked_block import ScheduleUnlockedBlock
from app.models.subject import Subject, subject_allowed_room
from app.models.subject_offering import SubjectOffering
from app.models.term import Term
from app.models.time_slot import TimeSlot

__all__ = [
    "AppUser",
    "AuditEvent",
    "AuthSession",
    "BlockSection",
    "BlockSectionOffering",
    "Building",
    "CourseProgram",
    "Curriculum",
    "CurriculumRequirement",
    "ExecutionJob",
    "ExtractedRule",
    "EvaluationBatch",
    "EvaluationRun",
    "Faculty",
    "faculty_preferred_subject",
    "subject_allowed_room",
    "HistoricalImportBatch",
    "HistoricalImportRejection",
    "GAConfiguration",
    "HardConstraintConfiguration",
    "HistoricalRecord",
    "HistoricalSnapshot",
    "JobStatus",
    "JobType",
    "Room",
    "RuleSet",
    "RuleSetFacultyMetric",
    "RuleSetThreshold",
    "Schedule",
    "ScheduleType",
    "ScheduleRecord",
    "ScheduleUnlockedBlock",
    "Subject",
    "SubjectOffering",
    "Term",
    "TimeSlot",
    "UserRole",
    "rule_set_source_snapshot",
    "rule_set_term",
]
