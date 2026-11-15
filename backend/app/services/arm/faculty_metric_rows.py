"""Database rows for per-teacher diagnostics recorded with a mined version.

The miner itself is untouched (the Colab notebooks load it standalone);
the two in-app persistence paths call `version_faculty_metrics` with the
transactions they mined and the candidate the miner returned.
"""

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from app.models.rule_set_faculty_metric import RuleSetFacultyMetric
from app.services.arm.faculty_metrics import FacultyStreamMetric, best_faculty_patterns
from app.services.arm.mining import CandidateRuleSet
from app.services.arm.preprocessing import StreamTrainingData


def version_faculty_metrics(
    prepared: Sequence[StreamTrainingData], candidate: CandidateRuleSet
) -> tuple[FacultyStreamMetric, ...]:
    """Every teacher's best pattern in each stream the miner actually mined."""
    metrics: list[FacultyStreamMetric] = []
    for data in prepared:
        result = candidate.result_for(data.stream)
        if result.skipped_reason is not None or not data.transactions:
            continue
        metrics.extend(
            best_faculty_patterns(
                data.stream,
                data.transactions,
                result.thresholds,
                result.training_term_count,
            )
        )
    return tuple(metrics)


def faculty_metric_rows(
    rule_set_id: UUID,
    metrics: Iterable[FacultyStreamMetric],
    *,
    persisted: set[tuple[str, UUID]],
    roster: Mapping[UUID, str],
) -> list[RuleSetFacultyMetric]:
    """Rows for an in-app mining run, whose faculty keys are roster IDs.

    ``persisted`` holds the (stream, faculty) pairs that kept at least one rule
    after catalog filtering. A history identity no longer on the roster keeps
    its key but gets no roster link.
    """
    rows: list[RuleSetFacultyMetric] = []
    for metric in metrics:
        faculty_id = metric.faculty_id
        known = faculty_id in roster
        rows.append(
            metric_row(
                rule_set_id,
                metric,
                faculty_id=faculty_id if known else None,
                faculty_name=(
                    roster[faculty_id]
                    if known
                    else f"Former faculty ({metric.faculty_key[:8]})"
                ),
                has_rule=(str(metric.stream), faculty_id) in persisted,
            )
        )
    return rows


def metric_row(
    rule_set_id: UUID,
    metric: FacultyStreamMetric,
    *,
    faculty_id: UUID | None,
    faculty_name: str,
    has_rule: bool,
) -> RuleSetFacultyMetric:
    return RuleSetFacultyMetric(
        rule_set_id=rule_set_id,
        pattern_type=str(metric.stream),
        source_faculty_key=metric.faculty_key,
        faculty_id=faculty_id,
        faculty_name=faculty_name,
        subject_code=metric.subject_code,
        room_code=metric.room_code,
        meeting_days=metric.meeting_days,
        time_slot=metric.time_slot,
        offering_count=metric.offering_count,
        support=metric.support,
        confidence=metric.confidence,
        lift=metric.lift,
        term_recurrence=metric.term_recurrence,
        meets_support=metric.meets_support,
        meets_confidence=metric.meets_confidence,
        meets_lift=metric.meets_lift,
        meets_term_recurrence=metric.meets_term_recurrence,
        has_rule=has_rule,
    )
