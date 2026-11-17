from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
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
    meeting_end,
    pattern_weekdays,
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
        flag_long_block_runs: bool = False,
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

        ``flag_long_block_runs`` reports a Block sitting in back-to-back classes
        for the Block run limit (6 hours by default) or more on one day. Only schedules being built or edited
        opt in; historical captures may legitimately hold longer runs.
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
        if flag_long_block_runs:
            calendar_conflicts.extend(
                self._long_block_run_conflicts(problem, candidate.assignments)
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
    def _long_block_run_conflicts(
        problem: SchedulingProblem, assignments: Sequence
    ) -> list[ConstraintConflict]:
        sections = {section.class_id: section for section in problem.classes}
        slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        day = datetime.min.date()
        meetings: dict[tuple[UUID, str], list[tuple]] = {}
        for assignment in assignments:
            section = sections.get(assignment.class_id)
            slot = slots.get(assignment.time_slot_id)
            if section is None or slot is None or not assignment.meeting_days:
                continue
            try:
                weekdays = pattern_weekdays(assignment.meeting_days)
            except ValueError:
                continue  # Reported above as outside the teaching calendar.
            start = datetime.combine(day, slot.start_time)
            end = datetime.combine(day, meeting_end(slot, assignment.meeting_days))
            for weekday in weekdays:
                meetings.setdefault((section.block_id, str(weekday)), []).append(
                    (start, end, assignment)
                )
        run_limit = timedelta(hours=problem.limits.block_run_limit_hours)
        conflicts = []
        for (block_id, weekday), entries in meetings.items():
            runs: list[list[tuple]] = []
            for entry in sorted(entries, key=lambda e: (e[0], e[1])):
                if runs and entry[0] <= max(e[1] for e in runs[-1]):
                    runs[-1].append(entry)
                else:
                    runs.append([entry])
            for run in runs:
                first, last = run[0][0], max(e[1] for e in run)
                if last - first < run_limit:
                    continue
                block_name = sections[run[0][2].class_id].block_name
                conflicts.append(
                    ConstraintConflict(
                        code=ConflictCode.BLOCK_CONSECUTIVE_EXCEEDED,
                        resource_key=(str(block_id), weekday),
                        class_ids=tuple(e[2].class_id for e in run),
                        time_slot_ids=tuple(e[2].time_slot_id for e in run),
                        message=(
                            f"{block_name} has back-to-back classes on {weekday} "
                            f"{first:%H:%M}-{last:%H:%M}; a Block's classes in a "
                            "row must total less than "
                            f"{problem.limits.block_run_limit_hours} hours."
                        ),
                    )
                )
        return conflicts

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
