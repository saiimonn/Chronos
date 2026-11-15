"""CHR-113 / CHR-GA-004 approved rule-backed runtime soft scoring."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain import Assignment, SchedulingProblem
from app.domain.meeting_patterns import meeting_label
from app.services.arm.preprocessing import PatternStream
from app.services.ga.fitness import HistoricalScoreBreakdown

if TYPE_CHECKING:
    from app.services.arm.providers import HistoricalGuidance

SCORING_POLICY = "equal_applicable_streams_v1"


class RuleBackedHistoricalScorer:
    """Average applicable Offering rewards, then equally weight present streams."""

    def __init__(self, guidance: HistoricalGuidance):
        self._guidance = guidance

    def score(
        self, problem: SchedulingProblem, assignments: Sequence[Assignment]
    ) -> HistoricalScoreBreakdown:
        classes = {item.class_id: item for item in problem.classes}
        slots = {item.time_slot_id: item for item in problem.time_slots}
        offerings = {}
        for assignment in assignments:
            if (
                assignment.class_id not in classes
                or assignment.time_slot_id not in slots
            ):
                raise ValueError("historical scoring received an unknown class or slot")
            offering_id = classes[assignment.class_id].offering_id
            placement = (
                assignment.faculty_id,
                assignment.room_code,
                assignment.meeting_days,
                assignment.time_slot_id,
            )
            if offering_id in offerings and offerings[offering_id][0] != placement:
                raise ValueError(
                    "shared Offering has inconsistent historical-score placements"
                )
            offerings.setdefault(offering_id, (placement, assignment))

        scores = {}
        for stream in PatternStream:
            rewards = []
            for _placement, assignment in offerings.values():
                subject = classes[assignment.class_id].subject_code
                rules = self._guidance.rules_for(stream, assignment.faculty_id)
                if stream == PatternStream.TEACHER_SUBJECT_ROOM:
                    rules = tuple(
                        rule for rule in rules if rule.subject_code == subject
                    )
                if not rules:
                    continue
                if stream == PatternStream.TEACHER_SUBJECT:
                    values = {"subject_code": subject}
                elif stream == PatternStream.TEACHER_DAY:
                    values = {"meeting_days": assignment.meeting_days}
                elif stream == PatternStream.TEACHER_TIME:
                    # A Friday-only class is scored on its whole window
                    # ("07:30-10:30"), which is how history records it.
                    values = {
                        "time_slot": meeting_label(
                            slots[assignment.time_slot_id], assignment.meeting_days
                        )
                    }
                else:
                    values = {
                        "subject_code": subject,
                        "room_code": assignment.room_code,
                    }
                reward = self._guidance.score_for(
                    stream, assignment.faculty_id, **values
                )
                rewards.append(Decimal(0) if reward is None else reward)
            scores[stream] = float(sum(rewards) / len(rewards)) if rewards else None
        applicable = [value for value in scores.values() if value is not None]
        return HistoricalScoreBreakdown(
            teacher_subject_score=scores[PatternStream.TEACHER_SUBJECT],
            teacher_day_score=scores[PatternStream.TEACHER_DAY],
            teacher_time_score=scores[PatternStream.TEACHER_TIME],
            teacher_subject_room_score=scores[PatternStream.TEACHER_SUBJECT_ROOM],
            historical_pattern_attainment=sum(applicable) / len(applicable)
            if applicable
            else 0.0,
        )
