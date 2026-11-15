"""Paired Blocks share their LEC and take each LAB at the same time.

Rooms and teachers of the two LABs must differ, which the existing overlap
rules already guarantee once the times match.
"""

from dataclasses import replace
from datetime import time
from uuid import UUID

import pytest

from app.domain import (
    Assignment,
    BlockClassDTO,
    BlockGenerationContext,
    BlockSectionDTO,
    CandidateSchedule,
    ConflictCode,
    FacultyDTO,
    LockedAssignment,
    RoomDTO,
    RoomType,
    TimeSlotDTO,
    build_lab_partners,
    build_offering_block_ids,
    partner_section_letter,
)
from app.services.ga.config import GAConfig
from app.services.ga.engine import GAEngine
from app.services.ga.repair import DeterministicRepairOperator, RepairContext
from app.services.jobs.codecs import (
    decode_block_generation_context,
    decode_scheduling_problem,
    encode_block_generation_context,
    encode_scheduling_problem,
)
from app.services.validation import ConstraintEngine
from app.tests.support.ga import (
    GA_TERM_ID,
    LAB_SUBJECT,
    LECTURE_SUBJECT,
    OFFERING_LECTURE_ID,
    SLOT_LAB_ID,
    make_ga_problem,
)

BLOCK_A = UUID("00000000-0000-0000-0000-00000000aa01")
BLOCK_B = UUID("00000000-0000-0000-0000-00000000aa02")
LEC_A = UUID("00000000-0000-0000-0000-00000000ac01")
LEC_B = UUID("00000000-0000-0000-0000-00000000ac02")
LAB_A = UUID("00000000-0000-0000-0000-00000000ac03")
LAB_B = UUID("00000000-0000-0000-0000-00000000ac04")
LAB_OFFERING_A = UUID("00000000-0000-0000-0000-00000000ad01")
LAB_OFFERING_B = UUID("00000000-0000-0000-0000-00000000ad02")
SLOT_EVENING_LAB = UUID("00000000-0000-0000-0000-00000000ae01")
FACULTY_IDS = tuple(UUID(f"00000000-0000-0000-0000-00000000af0{i}") for i in range(3))
LAB_ROOMS = ("<ROOM-LAB-1>", "<ROOM-LAB-2>")


def _class(class_id, block_id, letter, offering_id, subject_code) -> BlockClassDTO:
    return BlockClassDTO(
        class_id=class_id,
        block_id=block_id,
        term_id=GA_TERM_ID,
        offering_id=offering_id,
        group_no=1,
        subject_code=subject_code,
        program_code="<PROGRAM-1>",
        year_level=1,
        section_letter=letter,
        capacity=20,
    )


CLASSES = (
    _class(LEC_A, BLOCK_A, "A", OFFERING_LECTURE_ID, LECTURE_SUBJECT),
    _class(LAB_A, BLOCK_A, "A", LAB_OFFERING_A, LAB_SUBJECT),
    _class(LEC_B, BLOCK_B, "B", OFFERING_LECTURE_ID, LECTURE_SUBJECT),
    _class(LAB_B, BLOCK_B, "B", LAB_OFFERING_B, LAB_SUBJECT),
)


def make_paired_problem():
    """Blocks A and B share one LEC and each take the LAB in its own offering.

    Two lab slots, two lab rooms and three teachers leave plenty of ways to
    put the LABs at different times, so a match is the constraint's doing.
    """
    base = make_ga_problem(include_unstaffable=False)
    return replace(
        base,
        faculty=tuple(
            FacultyDTO(
                faculty_id=faculty_id,
                full_name=f"<FACULTY-{index}>",
                is_full_time=True,
                availability_mask="1111",
            )
            for index, faculty_id in enumerate(FACULTY_IDS)
        ),
        rooms=tuple(room for room in base.rooms if room.room_type is RoomType.LECTURE)
        + tuple(
            RoomDTO(
                room_code=code,
                building_name="<BUILDING-1>",
                capacity=25,
                room_type=RoomType.LABORATORY,
                expanded=False,
            )
            for code in LAB_ROOMS
        ),
        classes=CLASSES,
        time_slots=base.time_slots
        + (
            TimeSlotDTO(
                time_slot_id=SLOT_EVENING_LAB,
                term_id=GA_TERM_ID,
                label="<TIME-SLOT-EVENING-LAB>",
                start_time=time(18),
                end_time=time(20, 30),
                ordinal=3,
            ),
        ),
        offering_block_ids=build_offering_block_ids(CLASSES),
        lab_partners={LAB_A: LAB_B, LAB_B: LAB_A},
    )


def _lab(class_id, slot_id, days, room, faculty_index) -> Assignment:
    return Assignment(
        class_id=class_id,
        faculty_id=FACULTY_IDS[faculty_index],
        time_slot_id=slot_id,
        room_code=room,
        meeting_days=days,
    )


def _codes(report) -> list[ConflictCode]:
    return [conflict.code for conflict in report.conflicts]


@pytest.mark.parametrize(
    "letter, partner",
    [("A", "B"), ("B", "A"), ("C", "D"), ("H", "G"), ("Z", "Y"), ("AA", None), ("1", None)],
)
def test_sections_pair_by_consecutive_letters(letter, partner):
    assert partner_section_letter(letter) == partner


def test_build_lab_partners_links_only_letter_pairs_of_one_cohort():
    # C has no D; a 2B shares a letter pair with 1A but not a year level.
    lone_c = _class(UUID(int=99), UUID(int=98), "C", UUID(int=97), LAB_SUBJECT)
    other_year = replace(
        _class(UUID(int=96), UUID(int=95), "B", UUID(int=94), LAB_SUBJECT), year_level=2
    )
    partners = build_lab_partners(CLASSES + (lone_c, other_year), {LAB_SUBJECT})
    assert partners == {LAB_A: LAB_B, LAB_B: LAB_A}


def test_engine_reports_paired_labs_at_different_times_once():
    problem = make_paired_problem()
    report = ConstraintEngine().evaluate(
        problem,
        (
            _lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),
            _lab(LAB_B, SLOT_EVENING_LAB, "MWF", LAB_ROOMS[1], 1),
        ),
    )
    assert _codes(report) == [ConflictCode.PAIRED_LAB_MISMATCH]
    assert set(report.conflicts[0].class_ids) == {LAB_A, LAB_B}


def test_engine_reports_paired_labs_on_different_days():
    problem = make_paired_problem()
    report = ConstraintEngine().evaluate(
        problem,
        (
            _lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),
            _lab(LAB_B, SLOT_LAB_ID, "TTh", LAB_ROOMS[1], 1),
        ),
    )
    assert _codes(report) == [ConflictCode.PAIRED_LAB_MISMATCH]


def test_engine_accepts_paired_labs_at_one_time_in_separate_rooms():
    problem = make_paired_problem()
    report = ConstraintEngine().evaluate(
        problem,
        (
            _lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),
            _lab(LAB_B, SLOT_LAB_ID, "MWF", LAB_ROOMS[1], 1),
        ),
    )
    assert report.is_valid


def test_engine_still_rejects_paired_labs_sharing_a_room():
    problem = make_paired_problem()
    report = ConstraintEngine().evaluate(
        problem,
        (
            _lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),
            _lab(LAB_B, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 1),
        ),
    )
    assert _codes(report) == [ConflictCode.ROOM_OVERLAP]


def test_engine_checks_a_paired_lab_against_its_locked_partner():
    problem = replace(make_paired_problem(), classes=CLASSES[:2])
    lock = LockedAssignment(
        class_id=LAB_B,
        faculty_id=FACULTY_IDS[1],
        time_slot_id=SLOT_EVENING_LAB,
        room_code=LAB_ROOMS[1],
        meeting_days="TTh",
        block_id=BLOCK_B,
        offering_id=LAB_OFFERING_B,
        units=3,
    )
    report = ConstraintEngine().evaluate(
        problem,
        (_lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),),
        locked_assignments=(lock,),
    )
    assert _codes(report) == [ConflictCode.PAIRED_LAB_MISMATCH]


def test_repair_moves_paired_labs_onto_one_time():
    problem = make_paired_problem()
    candidate = CandidateSchedule(
        term_id=GA_TERM_ID,
        assignments=(
            _lab(LAB_A, SLOT_LAB_ID, "MWF", LAB_ROOMS[0], 0),
            _lab(LAB_B, SLOT_EVENING_LAB, "TTh", LAB_ROOMS[1], 1),
        ),
    )
    result = DeterministicRepairOperator().repair(candidate, RepairContext.build(problem))
    by_class = {a.class_id: a for a in result.candidate.assignments}
    lab_a, lab_b = by_class[LAB_A], by_class[LAB_B]
    assert (lab_a.time_slot_id, lab_a.meeting_days) == (
        lab_b.time_slot_id,
        lab_b.meeting_days,
    )
    assert lab_a.room_code != lab_b.room_code
    assert lab_a.faculty_id != lab_b.faculty_id
    assert result.report.is_valid


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_ga_places_paired_labs_together_and_lectures_as_one_session(seed):
    problem = make_paired_problem()
    result = GAEngine(problem, GAConfig(random_seed=seed, population_size=20)).run()
    by_class = {a.class_id: a for a in result.candidate.assignments}

    assert result.metrics.hard_conflict_count == 0
    assert set(by_class) == {LEC_A, LEC_B, LAB_A, LAB_B}
    assert by_class[LEC_A] == replace(by_class[LEC_B], class_id=LEC_A)
    lab_a, lab_b = by_class[LAB_A], by_class[LAB_B]
    assert (lab_a.time_slot_id, lab_a.meeting_days) == (
        lab_b.time_slot_id,
        lab_b.meeting_days,
    )
    assert lab_a.room_code != lab_b.room_code
    assert lab_a.faculty_id != lab_b.faculty_id


def _block_context(lock: LockedAssignment) -> BlockGenerationContext:
    problem = make_paired_problem()
    return BlockGenerationContext(
        term=problem.term,
        selected_block_id=BLOCK_A,
        selected_block=BlockSectionDTO(
            block_id=BLOCK_A,
            term_id=GA_TERM_ID,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=CLASSES[:2],
        locked_assignments=(lock,),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=build_offering_block_ids(CLASSES),
        lab_partners={LAB_A: LAB_B, LAB_B: LAB_A},
    )


PARTNER_LOCK = LockedAssignment(
    class_id=LAB_B,
    faculty_id=FACULTY_IDS[1],
    time_slot_id=SLOT_EVENING_LAB,
    room_code=LAB_ROOMS[1],
    meeting_days="TTh",
    block_id=BLOCK_B,
    offering_id=LAB_OFFERING_B,
    units=3,
)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_per_block_run_meets_when_the_locked_partner_lab_meets(seed):
    context = _block_context(PARTNER_LOCK)
    assert context.selected_problem.paired_lab_placements == {
        LAB_OFFERING_A: PARTNER_LOCK
    }
    result = GAEngine.from_block_context(
        context, GAConfig(random_seed=seed, population_size=20)
    ).run()
    lab_a = next(a for a in result.candidate.assignments if a.class_id == LAB_A)

    assert result.metrics.hard_conflict_count == 0
    assert (lab_a.time_slot_id, lab_a.meeting_days) == (SLOT_EVENING_LAB, "TTh")
    assert lab_a.room_code != PARTNER_LOCK.room_code
    assert lab_a.faculty_id != PARTNER_LOCK.faculty_id


def test_codecs_carry_lab_partners():
    problem = make_paired_problem()
    assert decode_scheduling_problem(encode_scheduling_problem(problem)).lab_partners == (
        problem.lab_partners
    )
    context = _block_context(PARTNER_LOCK)
    decoded = decode_block_generation_context(encode_block_generation_context(context))
    assert decoded.lab_partners == context.lab_partners


def test_codecs_decode_payloads_without_lab_partners():
    encoded = encode_scheduling_problem(make_paired_problem())
    del encoded["lab_partners"]
    assert decode_scheduling_problem(encoded).lab_partners == {}
