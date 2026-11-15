"""CHR-33 / CHR-ARM-002 candidate rule set persistence and retrieval."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    CourseProgram,
    Curriculum,
    CurriculumRequirement,
    ExtractedRule,
    Faculty,
    HistoricalRecord,
    HistoricalSnapshot,
    Room,
    RuleSet,
    RuleSetThreshold,
    Subject,
    Term,
)
from app.services.arm import (
    DatabaseRuleProvider,
    MiningProducedNoRulesError,
    MiningThresholds,
    NoActiveRuleSetError,
    PatternStream,
    RuleMiningService,
    load_historical_terms,
    load_historical_terms_by_ids,
)
from app.tests.support.history import add_to_curriculum

pytestmark = pytest.mark.postgres

PERMISSIVE = MiningThresholds(
    min_support=Decimal("0.2"),
    min_confidence=Decimal("0.5"),
    min_lift=Decimal("0"),
    min_term_recurrence=1,
)

# Four captured terms. Operational mining withholds no holdout (thesis §4.3), so
# every one of them is mined and recorded as an included term.
PERIODS = (
    ("2023-2024", 1),
    ("2023-2024", 2),
    ("2024-2025", 1),
    ("2024-2025", 2),
)


def ensure_term(session: Session, academic_year: str, term_number: int) -> Term:
    """Reuse the term row for this period when one is already there.

    The integration database is shared and holds whatever earlier committed
    tests left behind, so the fixture claims a period rather than assuming it is
    free.
    """
    term = session.scalars(
        select(Term).where(
            Term.academic_year == academic_year, Term.term_number == term_number
        )
    ).one_or_none()
    if term is None:
        term = Term(academic_year=academic_year, term_number=term_number, is_active=False)
        session.add(term)
        session.flush()
    return term


@pytest.fixture
def history(db_session: Session):
    """Catalog rows plus four captured terms of one instructor's habit."""
    faculty = Faculty(full_name="<FACULTY>", is_full_time=False, availability_mask="1")
    subject = Subject(
        subject_code="CS101", subject_title="<SUBJECT>", units=3, subject_type="LEC"
    )
    room = Room(
        room_code="LAB1",
        building_name="<BUILDING>",
        capacity=20,
        room_type="laboratory",
        expanded=False,
    )
    db_session.add_all([faculty, subject, room])
    db_session.flush()
    add_to_curriculum(db_session, subject.subject_code)

    terms = []
    for academic_year, term_number in PERIODS:
        term = ensure_term(db_session, academic_year, term_number)
        snapshot = HistoricalSnapshot(
            source_term_id=term.term_id,
            academic_year=academic_year,
            term_number=term_number,
        )
        db_session.add(snapshot)
        db_session.flush()
        db_session.add(
            HistoricalRecord(
                snapshot_id=snapshot.snapshot_id,
                source_faculty_id=faculty.faculty_id,
                faculty_name=faculty.full_name,
                subject_code=subject.subject_code,
                subject_title=subject.subject_title,
                room_code=room.room_code,
                meeting_days="MWF",
                time_slot="07:30-08:30",
            )
        )
        terms.append(term)
    db_session.flush()
    return faculty, subject, room, terms


def test_run_persists_a_draft_rule_set_with_its_thresholds(db_session, history):
    faculty, subject, room, terms = history

    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE, notes="<RUN>")

    rule_set = db_session.get(RuleSet, summary.rule_set_id)
    assert rule_set.status == "draft"
    assert rule_set.version == summary.version == 1
    assert rule_set.notes == "<RUN>"
    # Operational mining records exactly the terms it mined: all four.
    assert summary.included_term_count == 4
    assert {term.term_id for term in rule_set.included_terms} == {
        term.term_id for term in terms
    }

    thresholds = db_session.scalars(
        select(RuleSetThreshold).where(
            RuleSetThreshold.rule_set_id == summary.rule_set_id
        )
    ).all()
    assert {row.pattern_type for row in thresholds} == {
        str(stream) for stream in PatternStream
    }
    teacher_subject = next(
        row
        for row in thresholds
        if row.pattern_type == str(PatternStream.TEACHER_SUBJECT)
    )
    assert teacher_subject.min_support == Decimal("0.2000")
    assert teacher_subject.min_term_recurrence == 1
    assert teacher_subject.earliest_academic_year == "2018-2019"
    rooms = next(
        row
        for row in thresholds
        if row.pattern_type == str(PatternStream.TEACHER_SUBJECT_ROOM)
    )
    assert rooms.earliest_academic_year == "2023-2024"

    rules = db_session.scalars(
        select(ExtractedRule).where(ExtractedRule.rule_set_id == summary.rule_set_id)
    ).all()
    assert {row.pattern_type for row in rules} == {
        str(stream) for stream in PatternStream
    }
    assert all(row.faculty_id == faculty.faculty_id for row in rules)
    subject_rule = next(
        row for row in rules if row.pattern_type == str(PatternStream.TEACHER_SUBJECT)
    )
    # Four mined terms, one basket each: the pattern recurs in every one.
    assert subject_rule.subject_code == subject.subject_code
    assert subject_rule.confidence == Decimal("1.0000")
    assert subject_rule.term_recurrence == 4
    assert subject_rule.guidance_score == Decimal("1.0000")
    assert summary.persisted_rule_count == len(rules)
    assert summary.unknown_catalog_reference_count == 0


def _add_older_record(db_session, faculty, subject_code, room_code):
    """One offering in an older term, taught by `faculty` on its own day and slot."""
    older_term = ensure_term(db_session, "2022-2023", 1)
    snapshot = HistoricalSnapshot(
        source_term_id=older_term.term_id, academic_year="2022-2023", term_number=1
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        HistoricalRecord(
            snapshot_id=snapshot.snapshot_id,
            source_faculty_id=faculty.faculty_id,
            faculty_name=faculty.full_name,
            subject_code=subject_code,
            subject_title="<SUBJECT>",
            room_code=room_code,
            meeting_days="TTh",
            time_slot="10:30-11:30",
        )
    )
    db_session.flush()


def test_rules_referring_to_retired_catalog_entries_are_reported(db_session, history):
    faculty, subject, room, terms = history
    # A current subject taught once by an instructor who has since left the
    # faculty catalog, so the pattern is mined but cannot be persisted.
    departed = Faculty(
        faculty_id=uuid.uuid4(),
        full_name="<DEPARTED>",
        is_full_time=False,
        availability_mask="1",
    )
    _add_older_record(db_session, departed, subject.subject_code, room.room_code)

    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE)

    assert summary.unknown_catalog_reference_count > 0
    persisted = db_session.scalars(
        select(ExtractedRule.faculty_id).where(
            ExtractedRule.rule_set_id == summary.rule_set_id
        )
    ).all()
    assert departed.faculty_id not in persisted


def test_a_discontinued_curriculum_record_shapes_no_stream(db_session, history):
    faculty, subject, room, terms = history
    # The subject is still in the catalog but no reviewed curriculum requires
    # it, so its offering is not current history for any stream, including the
    # teacher-day and teacher-time rules that would not carry the subject.
    other = Faculty(full_name="<OTHER>", is_full_time=False, availability_mask="1")
    db_session.add_all(
        [
            other,
            Subject(
                subject_code="OLD101",
                subject_title="<OLD SUBJECT>",
                units=3,
                subject_type="LEC",
            ),
        ]
    )
    db_session.flush()
    _add_older_record(db_session, other, "OLD101", room.room_code)

    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE)

    mined_faculty = db_session.scalars(
        select(ExtractedRule.faculty_id).where(
            ExtractedRule.rule_set_id == summary.rule_set_id
        )
    ).all()
    assert mined_faculty
    assert other.faculty_id not in mined_faculty


def test_an_obsolete_curriculum_version_shapes_no_stream(db_session, history):
    faculty, subject, room, terms = history
    # 2018-2019 required OLD101; 2020-2021 replaced it. Both stay on file, but
    # in 2026-2027 every year level resolves to the replacement, so OLD101 is
    # retired even though some recorded curriculum still lists it.
    other = Faculty(full_name="<OTHER>", is_full_time=False, availability_mask="1")
    db_session.add_all(
        [
            other,
            Subject(
                subject_code="OLD101",
                subject_title="<OLD SUBJECT>",
                units=3,
                subject_type="LEC",
            ),
        ]
    )
    db_session.flush()
    db_session.add(CourseProgram(program_code="<OBS>", program_name="<OBS>"))
    db_session.flush()
    old = Curriculum(program_code="<OBS>", effective_year="2018-2019")
    new = Curriculum(program_code="<OBS>", effective_year="2020-2021")
    db_session.add_all([old, new])
    db_session.flush()
    db_session.add_all(
        [
            CurriculumRequirement(
                curriculum_id=old.curriculum_id,
                year_level=1,
                term_number=1,
                subject_code="OLD101",
            ),
            CurriculumRequirement(
                curriculum_id=new.curriculum_id,
                year_level=1,
                term_number=1,
                subject_code=subject.subject_code,
            ),
        ]
    )
    db_session.execute(update(Term).where(Term.is_active).values(is_active=False))
    ensure_term(db_session, "2026-2027", 1).is_active = True

    # The OLD101-only teacher's habit spans the same full-stream periods as
    # the current one, so day and time patterns would otherwise be mined.
    snapshots = db_session.scalars(
        select(HistoricalSnapshot).where(
            HistoricalSnapshot.source_term_id.in_([t.term_id for t in terms])
        )
    ).all()
    for snapshot in snapshots:
        db_session.add(
            HistoricalRecord(
                snapshot_id=snapshot.snapshot_id,
                source_faculty_id=other.faculty_id,
                faculty_name=other.full_name,
                subject_code="OLD101",
                subject_title="<OLD SUBJECT>",
                room_code=room.room_code,
                meeting_days="TTh",
                time_slot="10:30-11:30",
            )
        )
    db_session.flush()

    for loaded in (
        load_historical_terms(db_session),
        load_historical_terms_by_ids(
            db_session, [snapshot.snapshot_id for snapshot in snapshots]
        ),
    ):
        assert all(
            offering.subject_code != "OLD101"
            for term in loaded
            for offering in term.offerings
        )

    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE)

    mined = db_session.execute(
        select(ExtractedRule.pattern_type, ExtractedRule.faculty_id).where(
            ExtractedRule.rule_set_id == summary.rule_set_id
        )
    ).all()
    assert {stream for stream, _ in mined} >= {
        PatternStream.TEACHER_DAY.value,
        PatternStream.TEACHER_TIME.value,
    }
    assert other.faculty_id not in {faculty_id for _, faculty_id in mined}


def test_a_run_that_retains_nothing_writes_no_rule_set(db_session):
    before = db_session.scalar(select(func.count()).select_from(RuleSet))

    with pytest.raises(MiningProducedNoRulesError):
        RuleMiningService(db_session).create_candidate(PERMISSIVE)

    assert db_session.scalar(select(func.count()).select_from(RuleSet)) == before


def test_versions_increment_and_the_active_set_is_untouched(db_session, history):
    service = RuleMiningService(db_session)

    first = service.create_candidate(PERMISSIVE)
    db_session.get(RuleSet, first.rule_set_id).status = "active"
    db_session.flush()
    second = service.create_candidate(PERMISSIVE)

    assert second.version == first.version + 1
    assert db_session.get(RuleSet, first.rule_set_id).status == "active"
    assert db_session.get(RuleSet, second.rule_set_id).status == "draft"


def test_provider_serves_the_active_set_and_named_drafts(db_session, history):
    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE)
    provider = DatabaseRuleProvider(db_session)

    # A draft is retrievable for review, but never as the guidance a run may use.
    draft = provider.get_rules(summary.rule_set_id)
    assert draft.rule_set_id == summary.rule_set_id
    assert draft.version == summary.version
    assert len(draft.rules) == summary.persisted_rule_count
    with pytest.raises(NoActiveRuleSetError):
        provider.get_rules()

    db_session.get(RuleSet, summary.rule_set_id).status = "active"
    db_session.flush()

    active = provider.get_rules()
    assert active.rule_set_id == summary.rule_set_id
    faculty = history[0]
    assert active.is_applicable(PatternStream.TEACHER_SUBJECT, faculty.faculty_id)
    assert active.score_for(
        PatternStream.TEACHER_SUBJECT,
        faculty.faculty_id,
        subject_code="CS101",
    ) == Decimal("1.0000")
    assert active.score_for(
        PatternStream.TEACHER_SUBJECT_ROOM,
        faculty.faculty_id,
        subject_code="CS101",
        room_code="LAB1",
    ) == Decimal("1.0000")
    assert (
        active.score_for(
            PatternStream.TEACHER_SUBJECT,
            uuid.uuid4(),
            subject_code="CS101",
        )
        is None
    )


def test_run_records_every_teachers_best_pattern_per_stream(db_session, history):
    from app.models import RuleSetFacultyMetric

    faculty, subject, room, _terms = history

    summary = RuleMiningService(db_session).create_candidate(PERMISSIVE)

    rows = db_session.scalars(
        select(RuleSetFacultyMetric).where(
            RuleSetFacultyMetric.rule_set_id == summary.rule_set_id
        )
    ).all()
    by_stream = {
        row.pattern_type: row for row in rows if row.faculty_id == faculty.faculty_id
    }
    assert set(by_stream) == {stream.value for stream in PatternStream}
    subject_row = by_stream["teacher_subject"]
    assert subject_row.faculty_name == faculty.full_name
    assert subject_row.source_faculty_key == str(faculty.faculty_id)
    assert subject_row.subject_code == subject.subject_code
    assert subject_row.has_rule is True
    assert subject_row.meets_support and subject_row.meets_term_recurrence
    assert by_stream["teacher_subject_room"].room_code == room.room_code
