from collections.abc import Mapping, Sequence
from uuid import UUID

from app.domain import (
    BlockGenerationContext,
    CandidateSchedule,
    ConflictCode,
    ConstraintConflict,
    ConstraintReport,
    LockedAssignment,
    ScheduleValidationResult,
    SchedulingProblem,
)
from app.domain.meeting_patterns import (
    allowed_meeting_patterns,
    continuation_slots,
)
from app.services.validation.engine import ConstraintEngine, ConstraintEvaluationError



class ScheduleValidator:
    def __init__(self, constraint_engine: ConstraintEngine | None = None):
        self._constraint_engine = constraint_engine or ConstraintEngine()

    def validate(
        self,
        problem: SchedulingProblem,
        candidate: CandidateSchedule,
        locked_assignments: Sequence[LockedAssignment] | None = None,
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
        *,
        require_allowed_patterns: bool = False,
        flag_inactive_faculty: bool = False,
    ) -> ScheduleValidationResult:
        """Validate ``candidate`` against the problem's hard constraints.

        ``require_allowed_patterns`` also flags any class not meeting on one of
        its term's patterns (MW, TTh or F in terms 1-2, MTWTh in term 3). Only
        schedules being built or edited opt in: completed-term captures and
        evaluation scoring judge real historical placements, which may
        legitimately use other patterns.

        ``flag_inactive_faculty`` reports every class taught by a member marked
        as not currently teaching. Only schedules still being built opt in, so
        deactivating a teacher surfaces their classes for reassignment while a
        finalized schedule keeps the past teachers it was approved with.
        """
        if candidate.term_id != problem.term.term_id:
            raise ConstraintEvaluationError(
                "Candidate schedule does not belong to the problem term"
            )

        required_ids = {section.class_id for section in problem.classes}
        assigned_ids = {
            assignment.class_id
            for assignment in candidate.assignments
            if assignment.room_code is not None
        }
        unassigned_ids = tuple(sorted(required_ids - assigned_ids, key=str))
        report = self._constraint_engine.evaluate(
            problem,
            candidate.assignments,
            locked_assignments=locked_assignments,
            offering_block_ids=offering_block_ids,
        )
        # CHR-168: an operational schedule must fit the Monday-Sunday
        # calendar (Sunday is only a placeholder for the practicum/OJT). Reported as a hard conflict, not raised, so a stored
        # placement outside it stays editable while finalize and export refuse it.
        # evaluate() has already rejected malformed meeting days.
        calendar_conflicts = []
        sections = {section.class_id: section for section in problem.classes}
        slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        allowed_patterns = allowed_meeting_patterns(problem.term.term_number)
        allowed_label = (
            f"{', '.join(allowed_patterns[:-1])} or {allowed_patterns[-1]}"
            if len(allowed_patterns) > 1
            else allowed_patterns[0]
        )
        for assignment in candidate.assignments:
            try:
                ConstraintEngine.parse_teaching_days(assignment.meeting_days)
            except ConstraintEvaluationError:
                section = sections[assignment.class_id]
                calendar_conflicts.append(
                    ConstraintConflict(
                        code=ConflictCode.OUTSIDE_TEACHING_CALENDAR,
                        resource_key=(str(assignment.meeting_days),),
                        class_ids=(assignment.class_id,),
                        time_slot_ids=(assignment.time_slot_id,),
                        message=(
                            f"{section.group_label} ({section.block_name}) meets on "
                            f"{assignment.meeting_days}, outside the "
                            "Monday-Sunday teaching calendar."
                        ),
                    )
                )
                continue
            if (
                require_allowed_patterns
                and assignment.meeting_days not in allowed_patterns
            ):
                section = sections[assignment.class_id]
                calendar_conflicts.append(
                    ConstraintConflict(
                        code=ConflictCode.INVALID_MEETING_PATTERN,
                        resource_key=(str(assignment.meeting_days),),
                        class_ids=(assignment.class_id,),
                        time_slot_ids=(assignment.time_slot_id,),
                        message=(
                            f"{section.group_label} ({section.block_name}) meets on "
                            f"{assignment.meeting_days}; classes this term must "
                            f"meet {allowed_label}."
                        ),
                    )
                )
                continue
            # A Friday-only class holds both sessions back to back; the
            # second must still fit inside the teaching day.
            slot = slots.get(assignment.time_slot_id)
            if (
                slot is not None
                and continuation_slots(slot, assignment.meeting_days, problem.time_slots)
                is None
            ):
                section = sections[assignment.class_id]
                calendar_conflicts.append(
                    ConstraintConflict(
                        code=ConflictCode.OUTSIDE_TEACHING_CALENDAR,
                        resource_key=(str(assignment.meeting_days),),
                        class_ids=(assignment.class_id,),
                        time_slot_ids=(assignment.time_slot_id,),
                        message=(
                            f"{section.group_label} ({section.block_name}) meets "
                            f"{assignment.meeting_days} from {slot.start_time:%H:%M} "
                            "for two back-to-back sessions, which runs past the "
                            "end of the teaching day."
                        ),
                    )
                )
        if flag_inactive_faculty:
            calendar_conflicts.extend(
                self._inactive_faculty_conflicts(problem, candidate.assignments)
            )
        if calendar_conflicts:
            report = ConstraintReport(
                tuple(
                    sorted(
                        report.conflicts + tuple(calendar_conflicts),
                        key=ConstraintEngine._conflict_sort_key,
                    )
                )
            )
        return ScheduleValidationResult(
            required_class_count=len(required_ids),
            assigned_class_count=len(required_ids & assigned_ids),
            unassigned_class_ids=unassigned_ids,
            constraint_report=report,
        )

    @staticmethod
    def _inactive_faculty_conflicts(
        problem: SchedulingProblem, assignments: Sequence
    ) -> list[ConstraintConflict]:
        inactive = {
            member.faculty_id: member for member in problem.faculty if not member.is_active
        }
        if not inactive:
            return []
        sections = {section.class_id: section for section in problem.classes}
        conflicts = []
        for assignment in assignments:
            member = inactive.get(assignment.faculty_id)
            section = sections.get(assignment.class_id)
            if member is None or section is None:
                continue
            conflicts.append(
                ConstraintConflict(
                    code=ConflictCode.FACULTY_INACTIVE,
                    resource_key=(str(member.faculty_id),),
                    class_ids=(assignment.class_id,),
                    time_slot_ids=(assignment.time_slot_id,),
                    message=(
                        f"{member.full_name} teaches {section.group_label} "
                        f"({section.block_name}) but is marked as not currently "
                        "teaching. Assign another instructor."
                    ),
                )
            )
        return conflicts

    def validate_block_context(
        self,
        context: BlockGenerationContext,
        candidate: CandidateSchedule,
    ) -> ScheduleValidationResult:
        return self.validate(
            context.selected_problem,
            candidate,
            locked_assignments=context.locked_assignments,
            offering_block_ids=context.offering_block_ids,
        )
