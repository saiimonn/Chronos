import dataclasses
from uuid import UUID

import pytest

from app.domain import RoomType, SubjectType, TimeSlotDTO, build_offering_block_ids
from app.services.readiness import analyze_readiness
from app.tests.support.scheduling import OFFERING_ID, make_scheduling_problem


def codes(problem) -> set[str]:
    return {blocker.code for blocker in analyze_readiness(problem).blockers}


def blocker(problem, code):
    return next(
        item for item in analyze_readiness(problem).blockers if item.code == code
    )


def test_a_complete_problem_is_ready():
    report = analyze_readiness(make_scheduling_problem())

    assert report.ready is True
    assert report.blockers == ()


def test_missing_collections_are_each_reported():
    problem = dataclasses.replace(
        make_scheduling_problem(), time_slots=(), classes=(), rooms=()
    )

    assert {"NO_TIME_SLOTS", "NO_BLOCK_CLASSES", "NO_ROOMS"} <= codes(problem)



def test_a_mask_of_the_wrong_length_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base, faculty=(dataclasses.replace(base.faculty[0], availability_mask="00"),)
    )

    assert "FACULTY_MASK_LENGTH_MISMATCH" in codes(problem)


def test_an_unset_mask_is_open_and_not_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base, faculty=(dataclasses.replace(base.faculty[0], availability_mask=""),)
    )

    reported = codes(problem)
    assert "FACULTY_MASK_LENGTH_MISMATCH" not in reported
    assert "FACULTY_WITHOUT_AVAILABILITY" not in reported
    assert "SUBJECT_WITHOUT_COMPATIBLE_TIME_SLOT" not in reported


def test_inactive_faculty_neither_block_nor_add_capacity():
    base = make_scheduling_problem()
    retired = dataclasses.replace(
        base.faculty[0],
        faculty_id=UUID("00000000-0000-0000-0000-0000000000f9"),
        availability_mask="0" * len(base.time_slots),
        is_active=False,
    )
    with_retired = dataclasses.replace(base, faculty=base.faculty + (retired,))
    assert codes(with_retired) == codes(base)

    only_retired = dataclasses.replace(
        base, faculty=(dataclasses.replace(base.faculty[0], is_active=False),)
    )
    assert "INSUFFICIENT_TEACHING_CAPACITY" in codes(only_retired)


def test_a_mask_selecting_no_slot_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base, faculty=(dataclasses.replace(base.faculty[0], availability_mask="0"),)
    )

    assert "FACULTY_WITHOUT_AVAILABILITY" in codes(problem)


def test_a_section_larger_than_every_room_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base, rooms=(dataclasses.replace(base.rooms[0], capacity=10),)
    )

    assert blocker(problem, "SECTION_EXCEEDS_ALL_ROOM_CAPACITY").details == {
        "class_ids": [str(base.classes[0].class_id)]
    }


def test_a_lab_subject_without_a_lab_room_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base, subjects=(dataclasses.replace(base.subjects[0], subject_type=SubjectType.LAB),)
    )

    assert "LAB_SUBJECT_WITHOUT_LAB_ROOM" in codes(problem)


def test_a_lab_subject_with_a_lab_room_is_accepted():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base,
        subjects=(dataclasses.replace(base.subjects[0], subject_type=SubjectType.LAB),),
        rooms=(dataclasses.replace(base.rooms[0], room_type=RoomType.LABORATORY),),
    )

    assert "LAB_SUBJECT_WITHOUT_LAB_ROOM" not in codes(problem)


def test_a_lecture_subject_with_only_lab_rooms_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base,
        rooms=(dataclasses.replace(base.rooms[0], room_type=RoomType.LABORATORY),),
    )

    assert blocker(problem, "LEC_SUBJECT_WITHOUT_LECTURE_ROOM").details == {
        "subject_codes": [base.subjects[0].subject_code]
    }


def test_a_lecture_subject_with_a_lecture_room_is_accepted():
    problem = make_scheduling_problem()

    assert problem.subjects[0].subject_type is SubjectType.LEC
    assert problem.rooms[0].room_type is RoomType.LECTURE
    assert "LEC_SUBJECT_WITHOUT_LECTURE_ROOM" not in codes(problem)


def test_an_offered_subject_without_a_matching_duration_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base,
        time_slots=(
            TimeSlotDTO(
                time_slot_id=base.time_slots[0].time_slot_id,
                term_id=base.term.term_id,
                label="<WRONG-DURATION>",
                start_time=base.time_slots[0].start_time,
                end_time=base.time_slots[0].start_time.replace(hour=9),
                ordinal=0,
            ),
        ),
    )

    assert blocker(problem, "SUBJECT_WITHOUT_COMPATIBLE_TIME_SLOT").details == {
        "subject_codes": ["<SUBJECT-1>"]
    }


def test_matching_duration_must_be_available_to_an_instructor():
    base = make_scheduling_problem()
    lab_slot = dataclasses.replace(
        base.time_slots[0],
        time_slot_id=UUID("00000000-0000-0000-0000-000000000099"),
        label="<LAB-SLOT>",
        end_time=base.time_slots[0].end_time.replace(hour=10, minute=30),
        ordinal=1,
    )
    problem = dataclasses.replace(
        base,
        faculty=(
            dataclasses.replace(base.faculty[0], availability_mask="01"),
        ),
        time_slots=(base.time_slots[0], lab_slot),
    )

    assert blocker(problem, "SUBJECT_WITHOUT_COMPATIBLE_TIME_SLOT").details == {
        "subject_codes": ["<SUBJECT-1>"]
    }


def test_insufficient_teaching_capacity_is_reported():
    base = make_scheduling_problem()
    many_classes = tuple(
        dataclasses.replace(
            base.classes[0],
            class_id=UUID(f"00000000-0000-0000-0000-{i:012d}"),
            offering_id=UUID(int=100 + i),
            block_id=UUID(int=200 + i),
            group_no=i + 1,
            section_letter=chr(ord("A") + i),
        )
        for i in range(7)
    )
    # The lone instructor is part-time, so 21 units of demand outruns the
    # 17-unit cap that classification now derives.
    problem = dataclasses.replace(
        base, classes=many_classes,
        offering_block_ids=build_offering_block_ids(many_classes),
    )
    assert problem.faculty[0].is_full_time is False

    assert blocker(problem, "INSUFFICIENT_TEACHING_CAPACITY").details == {
        "required_units": 21,
        "available_units": 17,
    }


@pytest.mark.parametrize("offering_count", [4, 7])
def test_shared_offerings_count_teaching_units_once_per_offering(offering_count):
    """CHR-124 / CHR-BLK-007: shared attendance is not extra teaching load."""
    base = make_scheduling_problem()
    classes = tuple(
        dataclasses.replace(
            base.classes[0],
            class_id=UUID(int=1000 + 2 * offering + block),
            offering_id=UUID(int=2000 + offering),
            group_no=offering + 1,
            block_id=UUID(int=3000 + 2 * offering + block),
            section_letter=chr(ord("A") + 2 * offering + block),
        )
        for offering in range(offering_count)
        for block in range(2)
    )
    problem = dataclasses.replace(
        base, classes=classes, offering_block_ids=build_offering_block_ids(classes)
    )
    report = analyze_readiness(problem)
    if offering_count == 4:
        assert report.ready
        assert report.blockers == ()
    else:
        assert not report.ready
        assert blocker(problem, "INSUFFICIENT_TEACHING_CAPACITY").details == {
            "required_units": 21, "available_units": 17,
        }


def test_an_offering_block_mapping_that_drifted_from_the_classes_is_reported():
    base = make_scheduling_problem()
    problem = dataclasses.replace(base, offering_block_ids={OFFERING_ID: ()})

    assert blocker(problem, "INCONSISTENT_OFFERING_BLOCK_MAPPING").details == {
        "offering_ids": [str(OFFERING_ID)]
    }


def test_block_classes_without_any_offering_mapping_are_reported():
    problem = dataclasses.replace(make_scheduling_problem(), offering_block_ids={})

    assert "INCONSISTENT_OFFERING_BLOCK_MAPPING" in codes(problem)


def test_an_offering_combining_more_blocks_than_its_type_allows_is_reported():
    base = make_scheduling_problem()
    extra = tuple(
        dataclasses.replace(
            base.classes[0],
            class_id=UUID(f"00000000-0000-0000-0000-00000000010{index}"),
            block_id=UUID(f"00000000-0000-0000-0000-00000000020{index}"),
            section_letter=letter,
        )
        for index, letter in enumerate(("B", "C"))
    )
    classes = base.classes + extra
    problem = dataclasses.replace(
        base,
        classes=classes,
        offering_block_ids=build_offering_block_ids(classes),
    )

    # A lecture offering may combine two Blocks; this one carries three.
    assert blocker(problem, "OFFERING_EXCEEDS_MAX_BLOCKS").details == {
        "offering_id": str(OFFERING_ID),
        "block_count": 3,
        "max_blocks": 2,
    }


def test_inactive_rooms_add_no_capacity():
    base = make_scheduling_problem()
    problem = dataclasses.replace(
        base,
        rooms=tuple(dataclasses.replace(room, is_active=False) for room in base.rooms),
    )

    assert "NO_ROOMS" in codes(problem)


def test_classes_taking_an_inactive_subject_are_reported():
    base = make_scheduling_problem()
    offered = base.classes[0].subject_code
    problem = dataclasses.replace(
        base,
        subjects=tuple(
            dataclasses.replace(subject, is_active=False)
            if subject.subject_code == offered
            else subject
            for subject in base.subjects
        ),
    )

    assert blocker(problem, "INACTIVE_SUBJECT_OFFERED").details == {
        "subject_codes": [offered]
    }


def test_blocks_of_an_inactive_program_are_reported():
    base = make_scheduling_problem()
    program_code = base.classes[0].program_code
    problem = dataclasses.replace(
        base,
        programs=tuple(
            dataclasses.replace(program, is_active=False)
            if program.program_code == program_code
            else program
            for program in base.programs
        ),
    )

    assert blocker(problem, "INACTIVE_PROGRAM_BLOCKS").details == {
        "program_codes": [program_code]
    }


def test_capacity_blocker_message_states_the_unit_gap():
    base = make_scheduling_problem()
    classes = tuple(
        dataclasses.replace(
            base.classes[0],
            class_id=UUID(int=500 + i),
            offering_id=UUID(int=600 + i),
            block_id=UUID(int=700 + i),
            group_no=i + 1,
            section_letter=chr(ord("A") + i),
        )
        for i in range(7)
    )
    problem = dataclasses.replace(
        base, classes=classes, offering_block_ids=build_offering_block_ids(classes)
    )
    assert "by 4. Add 4 units" in blocker(problem, "INSUFFICIENT_TEACHING_CAPACITY").message

    retired = dataclasses.replace(
        base,
        faculty=tuple(dataclasses.replace(f, is_active=False) for f in base.faculty),
    )
    gap = blocker(retired, "INSUFFICIENT_TEACHING_CAPACITY")
    assert gap.details["available_units"] == 0
    assert f"at least {gap.details['required_units']} units" in gap.message
