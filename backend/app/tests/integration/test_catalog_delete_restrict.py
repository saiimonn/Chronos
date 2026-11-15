"""CHR-203 / CHR-FIX-030: parent deletes are refused by the database, not nulled by the ORM.

Without `passive_deletes` the ORM nulled child foreign keys before the parent
DELETE, so RESTRICT never fired and the delete became a 500 (or silently
orphaned rows). Each case here used to do that.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    Building,
    CourseProgram,
    Curriculum,
    ExtractedRule,
    Faculty,
    Room,
    RuleSet,
    Subject,
    SubjectOffering,
    Term,
    rule_set_term,
)
from app.services.catalog import (
    buildings,
    faculty,
    programs,
    rooms,
    subjects,
    terms,
)

pytestmark = pytest.mark.postgres


def _refused(call, *args) -> dict:
    with pytest.raises(APIError) as caught:
        call(*args)
    assert caught.value.status_code == 409
    assert caught.value.code == "DEPENDENT_RECORDS_EXIST"
    return caught.value.details["dependents"]


def _subject(db: Session, code: str = "RST 101") -> Subject:
    subject = Subject(subject_code=code, subject_title="t", units=3, subject_type="LEC")
    db.add(subject)
    db.flush()
    return subject


def _term(db: Session) -> Term:
    term = Term(academic_year="2090-2091", term_number=1, is_active=False)
    db.add(term)
    db.flush()
    return term


def _rule(db: Session, **consequent) -> tuple[Faculty, ExtractedRule]:
    person = Faculty(full_name="Rule Owner", is_full_time=False, availability_mask="1")
    rule_set = RuleSet(version=9001, status="draft")
    db.add_all([person, rule_set])
    db.flush()
    pattern = "teacher_subject_room" if "room_code" in consequent else "teacher_subject"
    rule = ExtractedRule(
        rule_set_id=rule_set.rule_set_id,
        pattern_type=pattern,
        faculty_id=person.faculty_id,
        support=0.5,
        confidence=0.5,
        lift=1,
        guidance_score=0.5,
        term_recurrence=1,
        **consequent,
    )
    db.add(rule)
    db.flush()
    return person, rule


def test_subject_with_an_unplaced_offering_is_retired_not_deleted(
    db_session: Session,
):
    subject = _subject(db_session)
    offering = SubjectOffering(
        term_id=_term(db_session).term_id, subject_code=subject.subject_code, group_no=1
    )
    db_session.add(offering)
    db_session.flush()
    subjects.deactivate_subject(db_session, subject.subject_code)
    assert db_session.get(Subject, subject.subject_code).is_active is False
    assert db_session.get(SubjectOffering, offering.offering_id) is not None


def test_term_with_offerings_and_no_blocks_is_refused(db_session: Session):
    subject = _subject(db_session)
    term = _term(db_session)
    db_session.add(SubjectOffering(
        term_id=term.term_id, subject_code=subject.subject_code, group_no=1
    ))
    db_session.flush()
    assert _refused(terms.delete_term, db_session, term.term_id) == {
        "subject_offerings": 1
    }


def test_term_named_by_a_rule_set_is_refused_and_keeps_provenance(db_session: Session):
    term = _term(db_session)
    db_session.add(RuleSet(version=9002, status="draft", included_terms=[term]))
    db_session.flush()
    assert _refused(terms.delete_term, db_session, term.term_id) == {"rule_sets": 1}
    links = db_session.execute(
        select(rule_set_term).where(rule_set_term.c.term_id == term.term_id)
    ).all()
    assert len(links) == 1


def test_program_with_a_curriculum_is_retired_not_deleted(db_session: Session):
    db_session.add(CourseProgram(program_code="RSTP", program_name="p"))
    db_session.flush()
    curriculum = Curriculum(program_code="RSTP", effective_year="2090-2091")
    db_session.add(curriculum)
    db_session.flush()
    programs.deactivate_program(db_session, "RSTP")
    assert db_session.get(CourseProgram, "RSTP").is_active is False
    assert db_session.get(Curriculum, curriculum.curriculum_id) is not None


def test_room_with_an_extracted_rule_is_retired_not_deleted(db_session: Session):
    subject = _subject(db_session)
    db_session.add(Room(
        room_code="RST-1", building_name="b", capacity=10, room_type="lecture"
    ))
    db_session.flush()
    _, rule = _rule(db_session, subject_code=subject.subject_code, room_code="RST-1")
    rooms.deactivate_room(db_session, "RST-1")
    assert db_session.get(Room, "RST-1").is_active is False
    assert db_session.get(ExtractedRule, rule.rule_id).room_code == "RST-1"


def test_subject_with_an_extracted_rule_is_retired_not_deleted(db_session: Session):
    subject = _subject(db_session)
    _, rule = _rule(db_session, subject_code=subject.subject_code)
    subjects.deactivate_subject(db_session, subject.subject_code)
    assert db_session.get(Subject, subject.subject_code).is_active is False
    assert db_session.get(ExtractedRule, rule.rule_id).subject_code == (
        subject.subject_code
    )


def test_faculty_with_an_extracted_rule_is_deactivated_not_deleted(
    db_session: Session,
):
    subject = _subject(db_session)
    person, rule = _rule(db_session, subject_code=subject.subject_code)
    faculty.deactivate_faculty(db_session, person.faculty_id)
    assert db_session.get(Faculty, person.faculty_id).is_active is False
    assert db_session.get(ExtractedRule, rule.rule_id) is not None


def test_building_retire_never_strands_its_active_rooms(db_session: Session):
    building = Building(building_name="Orphan Hall", building_code="ORPH")
    db_session.add(building)
    db_session.flush()
    room = Room(
        room_code="ORPH-1", building_id=building.building_id,
        building_name=building.building_name, capacity=10, room_type="lecture",
    )
    db_session.add(room)
    db_session.flush()
    assert _refused(
        buildings.deactivate_building, db_session, building.building_id
    ) == {"rooms": 1}
    db_session.refresh(room)
    assert room.building_id == building.building_id
    assert db_session.get(Building, building.building_id).is_active is True
