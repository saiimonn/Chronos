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
    SubjectDTO,
    SubjectType,
    TimeSlotDTO,
)
from app.services.validation import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    ConstraintEngine,
    ConstraintEvaluationError,
    ScheduleValidator,
)
from app.tests.support.ga import (
    COMBINED_CLASS_A_ID,
    COMBINED_CLASS_B_ID,
    FACULTY_FLEXIBLE_ID,
    LECTURE_ROOM,
    LECTURE_SUBJECT,
    SLOT_MORNING_ID,
    make_combined_offering_problem,
)
from app.tests.support.scheduling import make_scheduling_problem


TERM_ID = UUID("00000000-0000-0000-0000-000000000001")
FACULTY_1 = UUID("00000000-0000-0000-0000-000000000011")
FACULTY_2 = UUID("00000000-0000-0000-0000-000000000012")
SECTION_1 = UUID("00000000-0000-0000-0000-000000000021")
SECTION_2 = UUID("00000000-0000-0000-0000-000000000022")
SECTION_3 = UUID("00000000-0000-0000-0000-000000000023")
SLOT_1 = UUID("00000000-0000-0000-0000-000000000031")
SLOT_2 = UUID("00000000-0000-0000-0000-000000000032")
SLOT_3 = UUID("00000000-0000-0000-0000-000000000033")


def _problem():
    base = make_scheduling_problem()
    return replace(
        base,
        faculty=(
            FacultyDTO(FACULTY_1, "<FACULTY-1>", False, "111"),
            FacultyDTO(FACULTY_2, "<FACULTY-2>", False, "111"),
        ),
        subjects=(
            SubjectDTO("<SUBJECT-1>", "<SUBJECT-TITLE-1>", 3, SubjectType.LEC),
            SubjectDTO("<SUBJECT-2>", "<SUBJECT-TITLE-2>", 3, SubjectType.LEC),
            SubjectDTO("<SUBJECT-3>", "<SUBJECT-TITLE-3>", 3, SubjectType.LEC),
        ),
        rooms=(
            RoomDTO("<ROOM-1>", "<BUILDING-1>", 40, RoomType.LECTURE, False),
            RoomDTO("<ROOM-2>", "<BUILDING-1>", 40, RoomType.LECTURE, False),
        ),
        classes=(
            BlockClassDTO(
                class_id=SECTION_1,
                block_id=UUID("00000000-0000-0000-0000-0000000000a1"),
                term_id=TERM_ID,
                offering_id=UUID("00000000-0000-0000-0000-0000000000b1"),
                group_no=1,
                subject_code="<SUBJECT-1>",
                program_code="<PROGRAM-1>",
                year_level=1,
                section_letter="A",
                capacity=20,
            ),
            BlockClassDTO(
                class_id=SECTION_2,
                block_id=UUID("00000000-0000-0000-0000-0000000000a2"),
                term_id=TERM_ID,
                offering_id=UUID("00000000-0000-0000-0000-0000000000b2"),
                group_no=1,
                subject_code="<SUBJECT-2>",
                program_code="<PROGRAM-1>",
                year_level=1,
                section_letter="A",
                capacity=20,
            ),
            BlockClassDTO(
                class_id=SECTION_3,
                block_id=UUID("00000000-0000-0000-0000-0000000000a3"),
                term_id=TERM_ID,
                offering_id=UUID("00000000-0000-0000-0000-0000000000b3"),
                group_no=1,
                subject_code="<SUBJECT-3>",
                program_code="<PROGRAM-1>",
                year_level=1,
                section_letter="B",
                capacity=20,
            ),
        ),
        time_slots=(
            TimeSlotDTO(SLOT_1, TERM_ID, "08:00-09:30", time(8), time(9, 30), 0),
            TimeSlotDTO(SLOT_2, TERM_ID, "08:30-10:00", time(8, 30), time(10), 1),
            TimeSlotDTO(SLOT_3, TERM_ID, "09:30-11:00", time(9, 30), time(11), 2),
        ),
    )


def _assignment(
    class_id: UUID,
    *,
    faculty_id: UUID = FACULTY_1,
    time_slot_id: UUID = SLOT_1,
    room_code: str | None = "<ROOM-1>",
    meeting_days: str | None = "MWF",
) -> Assignment:
    return Assignment(
        class_id=class_id,
        faculty_id=faculty_id,
        time_slot_id=time_slot_id,
        room_code=room_code,
        meeting_days=meeting_days,
    )


def test_empty_and_single_assignments_are_valid():
    engine = ConstraintEngine()
    problem = _problem()

    empty = engine.evaluate(problem, ())
    single = engine.evaluate(problem, (_assignment(SECTION_1),))

    assert empty.is_valid is True
    assert empty.hard_conflict_count == 0
    assert single.conflicts == ()


def test_same_pair_reports_each_overlap_in_stable_order():
    report = ConstraintEngine().evaluate(
        _problem(),
        (_assignment(SECTION_2), _assignment(SECTION_1)),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
        ConflictCode.ROOM_OVERLAP,
        ConflictCode.COHORT_OVERLAP,
    )
    assert report.hard_conflict_count == 3
    assert report.conflicts[0].class_ids == (SECTION_1, SECTION_2)
    assert report.conflicts[0].resource_key == (str(FACULTY_1),)
    assert report.conflicts[0].message == (
        "Faculty <FACULTY-1> is double-booked on MWF 08:00-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and <SUBJECT-2> G1 (<PROGRAM-1>-1A)."
    )
    assert report.conflicts[1].message == (
        "Room <ROOM-1> is double-booked on MWF 08:00-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and <SUBJECT-2> G1 (<PROGRAM-1>-1A)."
    )
    assert report.conflicts[2].message == (
        "Block <PROGRAM-1>-1A has two classes at MWF 08:00-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and <SUBJECT-2> G1 (<PROGRAM-1>-1A)."
    )
    assert report.is_valid is False


@pytest.mark.parametrize(
    ("left", "right", "expected_code"),
    [
        (
            _assignment(SECTION_1, room_code="<ROOM-1>"),
            _assignment(
                SECTION_3,
                faculty_id=FACULTY_1,
                room_code="<ROOM-2>",
            ),
            ConflictCode.FACULTY_OVERLAP,
        ),
        (
            _assignment(SECTION_1, faculty_id=FACULTY_1),
            _assignment(SECTION_3, faculty_id=FACULTY_2),
            ConflictCode.ROOM_OVERLAP,
        ),
        (
            _assignment(SECTION_1, faculty_id=FACULTY_1, room_code="<ROOM-1>"),
            _assignment(SECTION_2, faculty_id=FACULTY_2, room_code="<ROOM-2>"),
            ConflictCode.COHORT_OVERLAP,
        ),
    ],
)
def test_each_overlap_constraint_is_independent(left, right, expected_code):
    report = ConstraintEngine().evaluate(_problem(), (left, right))

    assert tuple(conflict.code for conflict in report.conflicts) == (expected_code,)


def test_partial_day_and_clock_intersections_conflict():
    report = ConstraintEngine().evaluate(
        _problem(),
        (
            _assignment(SECTION_1, meeting_days="MW", time_slot_id=SLOT_1),
            _assignment(
                SECTION_3,
                meeting_days="MWF",
                time_slot_id=SLOT_2,
                room_code="<ROOM-2>",
            ),
        ),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
    )


@pytest.mark.parametrize(
    ("left_days", "right_days", "left_slot", "right_slot"),
    [
        ("MWF", "TTh", SLOT_1, SLOT_1),
        ("MWF", "MWF", SLOT_1, SLOT_3),
    ],
)
def test_disjoint_days_or_adjacent_slots_do_not_conflict(
    left_days, right_days, left_slot, right_slot
):
    report = ConstraintEngine().evaluate(
        _problem(),
        (
            _assignment(SECTION_1, meeting_days=left_days, time_slot_id=left_slot),
            _assignment(SECTION_2, meeting_days=right_days, time_slot_id=right_slot),
        ),
    )

    assert report.conflicts == ()


def test_missing_room_skips_only_room_overlap():
    report = ConstraintEngine().evaluate(
        _problem(),
        (
            _assignment(SECTION_1, room_code=None),
            _assignment(SECTION_2, room_code=None),
        ),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
        ConflictCode.COHORT_OVERLAP,
    )


def test_report_is_independent_of_problem_and_assignment_order():
    problem = _problem()
    assignments = (
        _assignment(SECTION_1),
        _assignment(SECTION_2),
        _assignment(SECTION_3),
    )
    engine = ConstraintEngine()

    forward = engine.evaluate(problem, assignments)
    reversed_input = engine.evaluate(
        replace(
            problem,
            classes=tuple(reversed(problem.classes)),
            time_slots=tuple(reversed(problem.time_slots)),
        ),
        tuple(reversed(assignments)),
    )

    assert forward == reversed_input


def test_three_assignments_produce_pairwise_faculty_conflicts():
    report = ConstraintEngine().evaluate(
        _problem(),
        (
            _assignment(SECTION_1, room_code=None),
            _assignment(SECTION_2, room_code=None),
            _assignment(SECTION_3, room_code=None),
        ),
    )

    faculty_conflicts = [
        conflict
        for conflict in report.conflicts
        if conflict.code == ConflictCode.FACULTY_OVERLAP
    ]
    assert len(faculty_conflicts) == 3


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        (_assignment(SECTION_1, meeting_days=None), "meeting_days is required"),
        (_assignment(SECTION_1, meeting_days="MX"), "Invalid meeting_days pattern"),
        (
            _assignment(UUID("00000000-0000-0000-0000-000000000099")),
            "Unknown class_id",
        ),
    ],
)
def test_invalid_assignment_data_is_rejected(assignment, message):
    with pytest.raises(ConstraintEvaluationError, match=message):
        ConstraintEngine().evaluate(_problem(), (assignment,))


def test_duplicate_section_assignment_is_rejected():
    assignment = _assignment(SECTION_1)

    with pytest.raises(ConstraintEvaluationError, match="Duplicate assignment"):
        ConstraintEngine().evaluate(_problem(), (assignment, assignment))


def test_faculty_can_teach_any_subject_without_eligibility_conflict():
    problem = _problem()
    report = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))

    assert report.conflicts == ()


def test_unavailable_time_slot_conflicts_by_ordinal_grid():
    problem = _problem()
    problem = replace(
        problem,
        faculty=(replace(problem.faculty[0], availability_mask="101"),)
        + problem.faculty[1:],
    )

    report = ConstraintEngine().evaluate(
        problem,
        (_assignment(SECTION_1, time_slot_id=SLOT_2),),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_UNAVAILABLE,
    )


def test_a_stale_mask_error_names_the_faculty_member():
    base = _problem()
    problem = replace(
        base,
        faculty=(replace(base.faculty[0], availability_mask="1"),) + base.faculty[1:],
    )

    with pytest.raises(ConstraintEvaluationError, match="Faculty <FACULTY-1>'s saved availability"):
        ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))


@pytest.mark.parametrize("mask", ("11", "10X"))
def test_availability_codec_rejects_masks_outside_slot_grid(mask):
    with pytest.raises(AvailabilityMaskError, match="one binary digit per time slot"):
        AvailabilityMaskCodec().available_slot_ids(mask, _problem().time_slots)


def test_availability_codec_uses_ordinal_order_not_input_order():
    available_ids = AvailabilityMaskCodec().available_slot_ids(
        "010",
        tuple(reversed(_problem().time_slots)),
    )

    assert available_ids == frozenset((SLOT_2,))


def _extra_lecture_sections(count: int, *, start: int = 4):
    """`count` extra 3-unit LEC sections, each with its own subject.

    Returned as (sections, subjects) so a test can splice them into the base
    problem. Every section is a cohort-A section of PROGRAM-1; keep them on
    distinct meeting days if the test does not want overlaps.
    """
    sections = []
    subjects = []
    for index in range(start, start + count):
        subject_code = f"<SUBJECT-{index}>"
        sections.append(
            BlockClassDTO(
                class_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
                block_id=UUID(f"00000000-0000-0000-0000-{index + 100:012d}"),
                term_id=TERM_ID,
                offering_id=UUID(f"00000000-0000-0000-0000-{index + 200:012d}"),
                group_no=1,
                subject_code=subject_code,
                program_code="<PROGRAM-1>",
                year_level=1,
                section_letter="A",
                capacity=20,
            )
        )
        subjects.append(
            SubjectDTO(subject_code, f"<SUBJECT-TITLE-{index}>", 3, SubjectType.LEC)
        )
    return tuple(sections), tuple(subjects)


# Part-time faculty cap is 17 units, so a workload case needs seven 3-unit
# sections to exceed it; five 3-unit and one 2-unit section sit on the boundary.
_DISTINCT_DAYS = ("M", "T", "W", "Th", "F", "Sa", "Su")


def test_workload_sums_subject_units_per_faculty():
    base = _problem()
    extra_sections, extra_subjects = _extra_lecture_sections(5)
    problem = replace(
        base,
        subjects=base.subjects + extra_subjects,
        classes=base.classes + extra_sections,
    )
    # SECTION_1 + SECTION_3 + five extras = seven 3-unit sections = 21 units,
    # over the 17-unit part-time cap. Distinct days keep every other
    # constraint clean so only the workload conflict fires.
    assigned = (SECTION_1, SECTION_3) + tuple(
        section.class_id for section in extra_sections
    )
    assignments = tuple(
        _assignment(class_id, meeting_days=day)
        for class_id, day in zip(assigned, _DISTINCT_DAYS)
    )

    report = ConstraintEngine().evaluate(problem, assignments)

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_WORKLOAD_EXCEEDED,
    )
    workload_conflict = report.conflicts[0]
    # class_ids follow the engine's stable resolution order (cohort, then
    # subject), not the order the assignments were handed in: the extras and
    # SECTION_1 are all cohort A, so cohort-B SECTION_3 sorts last.
    assert workload_conflict.class_ids == (
        SECTION_1,
        *(section.class_id for section in extra_sections),
        SECTION_3,
    )
    assert workload_conflict.message == (
        "Faculty <FACULTY-1> workload 21 exceeds maximum 17."
    )


def test_workload_equal_to_maximum_is_valid():
    base = _problem()
    extra_sections, extra_subjects = _extra_lecture_sections(4)
    extra_subjects = extra_subjects[:-1] + (replace(extra_subjects[-1], units=2),)
    problem = replace(
        base,
        subjects=base.subjects + extra_subjects,
        classes=base.classes + extra_sections,
    )
    # SECTION_1 + SECTION_3 + four extras, one of them 2 units = 17 units,
    # exactly the part-time cap, which is allowed: the check is `>`, not `>=`.
    assigned = (SECTION_1, SECTION_3) + tuple(
        section.class_id for section in extra_sections
    )
    assignments = tuple(
        _assignment(class_id, meeting_days=day)
        for class_id, day in zip(assigned, _DISTINCT_DAYS)
    )

    report = ConstraintEngine().evaluate(problem, assignments)

    assert report.is_valid


def test_room_capacity_uses_block_capacity():
    problem = _problem()
    problem = replace(
        problem,
        rooms=(replace(problem.rooms[0], capacity=19),) + problem.rooms[1:],
    )

    report = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.ROOM_CAPACITY_EXCEEDED,
    )


def test_room_capacity_equal_to_block_capacity_is_valid():
    problem = _problem()
    problem = replace(
        problem,
        rooms=(replace(problem.rooms[0], capacity=20),) + problem.rooms[1:],
    )

    assert ConstraintEngine().evaluate(
        problem,
        (_assignment(SECTION_1),),
    ).is_valid


def test_lab_subject_requires_laboratory_room():
    problem = _problem()
    problem = replace(
        problem,
        subjects=(replace(problem.subjects[0], subject_type=SubjectType.LAB),)
        + problem.subjects[1:],
        time_slots=(replace(problem.time_slots[0], end_time=time(10, 30)),)
        + problem.time_slots[1:],
    )

    mismatch = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))
    laboratory_problem = replace(
        problem,
        rooms=(replace(problem.rooms[0], room_type=RoomType.LABORATORY),)
        + problem.rooms[1:],
    )
    compatible = ConstraintEngine().evaluate(
        laboratory_problem,
        (_assignment(SECTION_1),),
    )

    assert tuple(conflict.code for conflict in mismatch.conflicts) == (
        ConflictCode.ROOM_TYPE_MISMATCH,
    )
    assert compatible.is_valid is True


def test_lecture_subject_requires_a_lecture_room():
    problem = _problem()
    assert problem.subjects[0].subject_type is SubjectType.LEC
    laboratory_problem = replace(
        problem,
        rooms=(replace(problem.rooms[0], room_type=RoomType.LABORATORY),)
        + problem.rooms[1:],
    )

    mismatch = ConstraintEngine().evaluate(
        laboratory_problem, (_assignment(SECTION_1),)
    )

    assert tuple(conflict.code for conflict in mismatch.conflicts) == (
        ConflictCode.ROOM_TYPE_MISMATCH,
    )
    assert mismatch.conflicts[0].message == (
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) is a lecture and requires a lecture "
        "room, but <ROOM-1> is laboratory."
    )
    assert ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),)).is_valid


def test_room_outside_allowed_rooms_is_a_hard_conflict():
    problem = _problem()
    room_code = problem.rooms[0].room_code

    def restrict(rooms):
        return replace(
            problem,
            subjects=(replace(problem.subjects[0], allowed_rooms=frozenset(rooms)),)
            + problem.subjects[1:],
        )

    report = ConstraintEngine().evaluate(restrict({"OTHER"}), (_assignment(SECTION_1),))

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.ROOM_NOT_ALLOWED,
    )
    assert ConstraintEngine().evaluate(
        restrict({room_code}), (_assignment(SECTION_1),)
    ).is_valid


def test_subject_duration_must_match_the_assigned_time_slot():
    problem = _problem()
    problem = replace(
        problem,
        time_slots=(replace(problem.time_slots[0], end_time=time(9)),)
        + problem.time_slots[1:],
    )

    report = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.SUBJECT_DURATION_MISMATCH,
    )
    assert "requires 90 minutes" in report.conflicts[0].message
    assert "lasts 60 minutes" in report.conflicts[0].message


@pytest.mark.parametrize(
    ("subject_type", "slot_end"),
    [(SubjectType.LEC, time(9, 30)), (SubjectType.LAB, time(10, 30))],
)
def test_exact_lab_and_lecture_durations_are_valid(subject_type, slot_end):
    problem = _problem()
    problem = replace(
        problem,
        subjects=(replace(problem.subjects[0], subject_type=subject_type),)
        + problem.subjects[1:],
        rooms=(
            replace(problem.rooms[0], room_type=subject_type.required_room_type),
        )
        + problem.rooms[1:],
        time_slots=(replace(problem.time_slots[0], end_time=slot_end),)
        + problem.time_slots[1:],
    )

    assert ConstraintEngine().evaluate(
        problem, (_assignment(SECTION_1),)
    ).is_valid


def test_all_eight_conflict_codes_have_stable_global_order():
    base = _problem()
    # Seven sections on FACULTY_1 (21 units) to clear the 18-unit part-time
    # cap, arranged so each of the eight codes fires exactly once:
    #   SECTION_1 + SECTION_2 share a day, slot, room and cohort -> the three
    #     overlap codes;
    #   SUBJECT-1 becomes a LAB in a lecture room -> room-type mismatch, and
    #     its 150-minute duration will not fit the 90-minute slot;
    #   the <SUBJECT-4> section sits in ROOM-2, shrunk below its enrollment
    #     -> room capacity exceeded;
    #   the <SUBJECT-7> section sits on SLOT_2, which the "101" mask marks
    #     unavailable.
    extra_sections, extra_subjects = _extra_lecture_sections(4)
    problem = replace(
        base,
        faculty=(
            replace(base.faculty[0], availability_mask="101"),
        ) + base.faculty[1:],
        subjects=(replace(base.subjects[0], subject_type=SubjectType.LAB),)
        + base.subjects[1:]
        + extra_subjects,
        classes=base.classes + extra_sections,
        rooms=base.rooms[:1] + (replace(base.rooms[1], capacity=19),),
    )
    section_4, section_5, section_6, section_7 = (
        section.class_id for section in extra_sections
    )
    assignments = (
        _assignment(SECTION_1, meeting_days="M"),
        _assignment(SECTION_2, meeting_days="M"),
        _assignment(SECTION_3, meeting_days="T"),
        _assignment(section_4, meeting_days="W", room_code="<ROOM-2>"),
        _assignment(section_5, meeting_days="Th"),
        _assignment(section_6, meeting_days="F"),
        _assignment(section_7, meeting_days="Su", time_slot_id=SLOT_2),
    )

    report = ConstraintEngine().evaluate(problem, assignments)

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
        ConflictCode.ROOM_OVERLAP,
        ConflictCode.COHORT_OVERLAP,
        ConflictCode.FACULTY_UNAVAILABLE,
        ConflictCode.FACULTY_WORKLOAD_EXCEEDED,
        ConflictCode.ROOM_CAPACITY_EXCEEDED,
        ConflictCode.ROOM_TYPE_MISMATCH,
        ConflictCode.SUBJECT_DURATION_MISMATCH,
    )

    reversed_report = ConstraintEngine().evaluate(
        replace(
            problem,
            faculty=tuple(reversed(problem.faculty)),
            subjects=tuple(reversed(problem.subjects)),
            rooms=tuple(reversed(problem.rooms)),
            classes=tuple(reversed(problem.classes)),
            time_slots=tuple(reversed(problem.time_slots)),
        ),
        tuple(reversed(assignments)),
    )
    assert reversed_report == report


def _candidate(*assignments: Assignment, term_id: UUID = TERM_ID) -> CandidateSchedule:
    return CandidateSchedule(term_id=term_id, assignments=assignments)


def test_final_validator_allows_only_complete_conflict_free_schedule():
    assignments = (
        _assignment(SECTION_1, meeting_days="M", time_slot_id=SLOT_1),
        _assignment(SECTION_2, meeting_days="T", time_slot_id=SLOT_2),
        _assignment(SECTION_3, meeting_days="W", time_slot_id=SLOT_3),
    )

    result = ScheduleValidator().validate(_problem(), _candidate(*assignments))

    assert result.is_complete is True
    assert result.assigned_class_count == result.required_class_count == 3
    assert result.hard_conflict_count == 0
    assert result.is_export_eligible is True


def test_final_validator_rejects_incomplete_or_conflicting_schedule_for_export():
    incomplete = ScheduleValidator().validate(
        _problem(),
        _candidate(_assignment(SECTION_1)),
    )
    missing_room = ScheduleValidator().validate(
        _problem(),
        _candidate(_assignment(SECTION_1, room_code=None)),
    )
    conflicting = ScheduleValidator().validate(
        _problem(),
        _candidate(
            _assignment(SECTION_1),
            _assignment(SECTION_2),
            _assignment(SECTION_3),
        ),
    )

    assert incomplete.unassigned_class_ids == (SECTION_2, SECTION_3)
    assert incomplete.is_export_eligible is False
    assert missing_room.unassigned_class_ids == (SECTION_1, SECTION_2, SECTION_3)
    assert missing_room.is_export_eligible is False
    assert conflicting.is_complete is True
    assert conflicting.hard_conflict_count > 0
    assert conflicting.is_export_eligible is False


def test_final_validator_rejects_candidate_from_another_term():
    with pytest.raises(ConstraintEvaluationError, match="does not belong"):
        ScheduleValidator().validate(
            _problem(),
            _candidate(
                _assignment(SECTION_1),
                term_id=UUID("00000000-0000-0000-0000-000000000099"),
            ),
        )


@pytest.mark.parametrize("meeting_days", ["Sa", "Su", "MSu"])
def test_weekend_days_are_inside_the_teaching_calendar(meeting_days):
    """Sunday joined the calendar for practicum classes."""
    result = ScheduleValidator().validate(
        _problem(),
        _candidate(_assignment(SECTION_1, meeting_days=meeting_days)),
    )

    assert ConflictCode.OUTSIDE_TEACHING_CALENDAR not in [
        conflict.code for conflict in result.constraint_report.conflicts
    ]


def test_a_mixed_weekday_and_sunday_pattern_is_not_an_allowed_pattern():
    """A practicum meets on Sa or Su alone; MSu is still refused on edit."""
    first = _assignment(SECTION_1, meeting_days="MSu")
    second = _assignment(SECTION_2, meeting_days="Su")
    result = ScheduleValidator().validate(
        _problem(), _candidate(first, second), require_allowed_patterns=True
    )

    flagged = [
        conflict.class_ids
        for conflict in result.constraint_report.conflicts
        if conflict.code is ConflictCode.INVALID_MEETING_PATTERN
    ]
    assert flagged == [(SECTION_1,)]


def test_final_validator_does_not_export_empty_problem():
    problem = replace(_problem(), classes=())

    result = ScheduleValidator().validate(problem, _candidate())

    assert result.required_class_count == 0
    assert result.is_complete is False
    assert result.is_export_eligible is False


# --- Combined Block sessions -------------------------------------------------
#
# Two Blocks of one offering attend the same session, so sharing a faculty
# member, a room, and a slot is the intended shape rather than a clash.


def _combined_assignments(**overrides) -> tuple[Assignment, ...]:
    placement = {
        "faculty_id": FACULTY_FLEXIBLE_ID,
        "time_slot_id": SLOT_MORNING_ID,
        "room_code": LECTURE_ROOM,
        "meeting_days": "MWF",
    } | overrides
    return tuple(
        Assignment(class_id=class_id, **placement)
        for class_id in (COMBINED_CLASS_A_ID, COMBINED_CLASS_B_ID)
    )


def test_blocks_of_one_offering_may_share_a_faculty_room_and_slot():
    report = ConstraintEngine().evaluate(
        make_combined_offering_problem(), _combined_assignments()
    )

    assert report.conflicts == ()


def test_room_must_seat_the_combined_enrollment_of_an_offering():
    problem = make_combined_offering_problem()
    problem = replace(
        problem,
        rooms=tuple(
            replace(room, capacity=39) if room.room_code == LECTURE_ROOM else room
            for room in problem.rooms
        ),
    )

    report = ConstraintEngine().evaluate(problem, _combined_assignments())

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.ROOM_CAPACITY_EXCEEDED,
    )
    conflict = report.conflicts[0]
    assert conflict.class_ids == (COMBINED_CLASS_A_ID, COMBINED_CLASS_B_ID)
    assert "below combined Block enrollment 40" in conflict.message


def test_a_combined_session_counts_its_units_once_towards_workload():
    problem = make_combined_offering_problem()
    # Ten units per Block would breach the 18-unit part-time cap if the two
    # Blocks were counted separately; one combined session must not.
    problem = replace(
        problem,
        subjects=tuple(
            replace(subject, units=10)
            if subject.subject_code == LECTURE_SUBJECT
            else subject
            for subject in problem.subjects
        ),
    )

    report = ConstraintEngine().evaluate(problem, _combined_assignments())

    assert report.conflicts == ()


def test_blocks_of_one_offering_split_across_rooms_block_export():
    # One teacher in two rooms at once must not pass as a combined session.
    problem = make_combined_offering_problem()
    block_a, block_b = _combined_assignments()
    other_room = next(
        room.room_code for room in problem.rooms if room.room_code != LECTURE_ROOM
    )
    assignments = (block_a, replace(block_b, room_code=other_room))

    report = ConstraintEngine().evaluate(problem, assignments)

    # The other room is the fixture's laboratory, which a lecture never takes.
    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
        ConflictCode.ROOM_TYPE_MISMATCH,
        ConflictCode.SHARED_OFFERING_MISMATCH,
    )
    assert not ScheduleValidator().validate(
        problem, _candidate(*assignments, term_id=problem.term.term_id)
    ).is_export_eligible


def test_blocks_of_one_offering_at_different_times_are_inconsistent():
    report = ConstraintEngine().evaluate(
        make_combined_offering_problem(),
        (
            _combined_assignments()[0],
            replace(_combined_assignments()[1], meeting_days="TTh"),
        ),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.SHARED_OFFERING_MISMATCH,
    )


# --- CHR-86 / CHR-SCH-010 Lock-safe Block evaluation tests -------------------


def test_locked_assignment_faculty_overlap_raises_hard_conflict():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code="<ROOM-2>",
        meeting_days="M",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert report.hard_conflict_count == 1
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.code == ConflictCode.FACULTY_OVERLAP
    assert conflict.class_ids == (SECTION_1, locked.class_id)
    assert conflict.time_slot_ids == (SLOT_1, SLOT_1)


def test_locked_assignment_room_overlap_raises_hard_conflict():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_2,
        time_slot_id=SLOT_1,
        room_code=candidate_assignment.room_code,
        meeting_days="W",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert report.hard_conflict_count == 1
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.code == ConflictCode.ROOM_OVERLAP
    assert conflict.class_ids == (SECTION_1, locked.class_id)


def test_locked_assignment_disjoint_days_or_times_do_not_conflict():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked_diff_day = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code=candidate_assignment.room_code,
        meeting_days="TTh",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    locked_diff_time = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000098"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_3,
        room_code=candidate_assignment.room_code,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked_diff_day, locked_diff_time),
    )

    assert report.conflicts == ()


def test_locked_assignment_same_offering_does_not_conflict():
    problem = _problem()
    section = problem.classes[0]
    candidate_assignment = _assignment(section.class_id, meeting_days="MWF", time_slot_id=SLOT_1)
    other_block_id = UUID("00000000-0000-0000-0000-000000000088")
    problem = replace(
        problem,
        offering_block_ids={section.offering_id: (section.block_id, other_block_id)},
    )
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code=candidate_assignment.room_code,
        meeting_days="MWF",
        block_id=other_block_id,
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert report.conflicts == ()


def test_locked_assignment_of_same_offering_by_identity_does_not_conflict():
    problem = _problem()
    section = problem.classes[0]
    candidate_assignment = _assignment(section.class_id, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code=candidate_assignment.room_code,
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
        offering_id=section.offering_id,
        units=3,
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert report.conflicts == ()


def test_locked_unrelated_offering_in_a_shared_block_still_conflicts():
    # The lock's Block also takes the candidate's Offering, but the locked
    # class is a different Offering, so sharing a faculty member is a clash.
    problem = _problem()
    section = problem.classes[0]
    other_block_id = UUID("00000000-0000-0000-0000-000000000088")
    problem = replace(
        problem,
        offering_block_ids={section.offering_id: (section.block_id, other_block_id)},
    )
    candidate_assignment = _assignment(section.class_id, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code="<ROOM-2>",
        meeting_days="M",
        block_id=other_block_id,
        offering_id=UUID("00000000-0000-0000-0000-0000000000f1"),
        units=3,
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_OVERLAP,
    )


def test_locked_teaching_load_counts_towards_workload():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="M", time_slot_id=SLOT_1)
    faculty = next(
        item for item in problem.faculty if item.faculty_id == candidate_assignment.faculty_id
    )
    locked_units = faculty.max_workload
    lock = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code="<ROOM-2>",
        meeting_days="T",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
        offering_id=UUID("00000000-0000-0000-0000-0000000000f1"),
        units=locked_units,
    )

    report = ConstraintEngine().evaluate(
        problem, (candidate_assignment,), locked_assignments=(lock,)
    )

    assert tuple(conflict.code for conflict in report.conflicts) == (
        ConflictCode.FACULTY_WORKLOAD_EXCEEDED,
    )
    assert f"exceeds maximum {faculty.max_workload}" in report.conflicts[0].message


def test_locked_assignment_unknown_time_slot_raises():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=UUID("00000000-0000-0000-0000-000000000999"),
        room_code="<ROOM-1>",
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    with pytest.raises(ConstraintEvaluationError, match="Unknown time_slot_id"):
        ConstraintEngine().evaluate(
            problem,
            (candidate_assignment,),
            locked_assignments=(locked,),
        )


def test_schedule_validator_validate_block_context_enforces_locks():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code="<ROOM-2>",
        meeting_days="MWF",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    context = BlockGenerationContext(
        term=problem.term,
        selected_block_id=UUID("00000000-0000-0000-0000-000000000001"),
        selected_block=BlockSectionDTO(
            block_id=UUID("00000000-0000-0000-0000-000000000001"),
            term_id=problem.term.term_id,
            program_code="<PROGRAM-1>",
            year_level=1,
            section_letter="A",
            capacity=20,
        ),
        selected_classes=(problem.classes[0],),
        locked_assignments=(locked,),
        faculty=problem.faculty,
        subjects=problem.subjects,
        rooms=problem.rooms,
        programs=problem.programs,
        time_slots=problem.time_slots,
        offering_block_ids=problem.offering_block_ids,
        base_schedule_id=None,
    )

    result = ScheduleValidator().validate_block_context(
        context,
        _candidate(candidate_assignment),
    )

    assert result.is_complete is True
    assert result.hard_conflict_count == 1
    assert result.is_export_eligible is False


def test_locked_assignment_empty_meeting_days_is_skipped():
    problem = _problem()
    candidate_assignment = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    locked = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=candidate_assignment.faculty_id,
        time_slot_id=SLOT_1,
        room_code="<ROOM-2>",
        meeting_days="",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    report = ConstraintEngine().evaluate(
        problem,
        (candidate_assignment,),
        locked_assignments=(locked,),
    )

    assert report.conflicts == ()


def test_schedule_validator_validate_block_context_combined_offering_no_conflicts():
    combined_problem = make_combined_offering_problem()
    class_a = combined_problem.classes[0]
    class_b = combined_problem.classes[1]
    candidate_assignment = Assignment(
        class_id=class_a.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
    )
    locked = LockedAssignment(
        class_id=class_b.class_id,
        faculty_id=FACULTY_FLEXIBLE_ID,
        time_slot_id=SLOT_MORNING_ID,
        room_code=LECTURE_ROOM,
        meeting_days="MWF",
        block_id=class_b.block_id,
    )
    context = BlockGenerationContext(
        term=combined_problem.term,
        selected_block_id=class_a.block_id,
        selected_block=BlockSectionDTO(
            block_id=class_a.block_id,
            term_id=combined_problem.term.term_id,
            program_code=class_a.program_code,
            year_level=class_a.year_level,
            section_letter=class_a.section_letter,
            capacity=class_a.capacity,
        ),
        selected_classes=(class_a,),
        locked_assignments=(locked,),
        faculty=combined_problem.faculty,
        subjects=combined_problem.subjects,
        rooms=combined_problem.rooms,
        programs=combined_problem.programs,
        time_slots=combined_problem.time_slots,
        offering_block_ids=combined_problem.offering_block_ids,
        base_schedule_id=None,
    )

    result = ScheduleValidator().validate_block_context(
        context,
        _candidate(candidate_assignment, term_id=combined_problem.term.term_id),
    )

    assert result.is_complete is True
    assert result.hard_conflict_count == 0
    assert result.is_export_eligible is True




# --- CHR-194 / CHR-FIX-023 Readable conflict messages ------------------------


def test_partial_overlap_names_the_shared_days_and_window():
    report = ConstraintEngine().evaluate(
        _problem(),
        (
            _assignment(SECTION_1, meeting_days="MTh", time_slot_id=SLOT_1),
            _assignment(SECTION_3, meeting_days="TThF", time_slot_id=SLOT_2),
        ),
    )
    messages = {conflict.code: conflict.message for conflict in report.conflicts}
    assert messages[ConflictCode.FACULTY_OVERLAP] == (
        "Faculty <FACULTY-1> is double-booked on Th 08:30-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and <SUBJECT-3> G1 (<PROGRAM-1>-1B)."
    )
    assert messages[ConflictCode.ROOM_OVERLAP].startswith(
        "Room <ROOM-1> is double-booked on Th 08:30-09:30: "
    )
    assert ConflictCode.COHORT_OVERLAP not in messages


def test_unavailable_and_workload_messages_name_faculty_and_class():
    problem = replace(
        _problem(),
        faculty=(
            FacultyDTO(FACULTY_1, "<FACULTY-1>", False, "011"),
            FacultyDTO(FACULTY_2, "<FACULTY-2>", False, "111"),
        ),
    )
    report = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))

    assert [conflict.message for conflict in report.conflicts] == [
        "Faculty <FACULTY-1> is unavailable on MWF 08:00-09:30, but is "
        "assigned <SUBJECT-1> G1 (<PROGRAM-1>-1A)."
    ]


def test_room_type_duration_and_capacity_messages_name_the_class():
    problem = replace(
        _problem(),
        subjects=(
            SubjectDTO("<SUBJECT-1>", "<SUBJECT-TITLE-1>", 3, SubjectType.LAB),
            *_problem().subjects[1:],
        ),
        rooms=(RoomDTO("<ROOM-1>", "<BUILDING-1>", 10, RoomType.LECTURE, False),),
    )
    report = ConstraintEngine().evaluate(problem, (_assignment(SECTION_1),))
    messages = {conflict.code: conflict.message for conflict in report.conflicts}

    label = "<SUBJECT-1> G1 (<PROGRAM-1>-1A)"
    assert messages[ConflictCode.ROOM_TYPE_MISMATCH] == (
        f"{label} is a lab and requires a laboratory room, but <ROOM-1> is "
        "lecture."
    )
    assert messages[ConflictCode.SUBJECT_DURATION_MISMATCH] == (
        f"{label} requires 150 minutes, but its time slot 08:00-09:30 lasts "
        "90 minutes."
    )
    assert messages[ConflictCode.ROOM_CAPACITY_EXCEEDED] == (
        f"Room <ROOM-1> capacity 10 is below section enrollment 20 for {label}."
    )


def test_locked_overlap_messages_name_the_candidate_class_and_clash():
    candidate = _assignment(SECTION_1, meeting_days="MWF", time_slot_id=SLOT_1)
    lock = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_1,
        time_slot_id=SLOT_2,
        room_code="<ROOM-1>",
        meeting_days="WTh",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )
    report = ConstraintEngine().evaluate(
        _problem(), (candidate,), locked_assignments=(lock,)
    )

    assert [conflict.message for conflict in report.conflicts] == [
        "Faculty <FACULTY-1> is double-booked on W 08:30-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and a class already placed in another Block.",
        "Room <ROOM-1> is double-booked on W 08:30-09:30: "
        "<SUBJECT-1> G1 (<PROGRAM-1>-1A) and a class already placed in another Block.",
    ]


def test_pattern_message_names_the_class():
    result = ScheduleValidator().validate(
        _problem(),
        _candidate(_assignment(SECTION_1, meeting_days="MSu")),
        require_allowed_patterns=True,
    )

    messages = [
        c.message
        for c in result.constraint_report.conflicts
        if c.code is ConflictCode.INVALID_MEETING_PATTERN
    ]
    assert len(messages) == 1
    assert messages[0].startswith("<SUBJECT-1> G1 (<PROGRAM-1>-1A) meets on MSu;")
    assert messages[0].endswith("Sa or Su.")


def _run_problem(*classes: tuple[SubjectType, time, time]):
    """One full-time faculty member with one class per (type, start, end)."""
    base = _problem()
    sections, subjects, slots, assignments = [], [], [], []
    for index, (subject_type, start, end) in enumerate(classes, start=40):
        section = replace(
            base.classes[0],
            class_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
            offering_id=UUID(f"00000000-0000-0000-0000-{index + 200:012d}"),
            subject_code=f"<SUBJECT-{index}>",
        )
        sections.append(section)
        subjects.append(
            SubjectDTO(f"<SUBJECT-{index}>", "<TITLE>", 3, subject_type)
        )
        slot_id = UUID(f"00000000-0000-0000-0000-{index + 300:012d}")
        slots.append(
            TimeSlotDTO(slot_id, TERM_ID, "<SLOT>", start, end, len(slots))
        )
        assignments.append(
            _assignment(section.class_id, time_slot_id=slot_id, meeting_days="M")
        )
    problem = replace(
        base,
        faculty=(FacultyDTO(FACULTY_1, "<FACULTY-1>", True, "1" * len(slots)),),
        subjects=tuple(subjects),
        classes=tuple(sections),
        time_slots=tuple(slots),
    )
    return problem, tuple(assignments)


def _consecutive(report):
    return [
        conflict
        for conflict in report.conflicts
        if conflict.code is ConflictCode.FACULTY_CONSECUTIVE_EXCEEDED
    ]


LEC, LAB = SubjectType.LEC, SubjectType.LAB


def test_three_lectures_in_a_row_are_allowed_and_a_fourth_is_not():
    three = _run_problem(
        (LEC, time(8), time(9, 30)),
        (LEC, time(9, 30), time(11)),
        (LEC, time(11), time(12, 30)),
    )
    four = _run_problem(
        (LEC, time(8), time(9, 30)),
        (LEC, time(9, 30), time(11)),
        (LEC, time(11), time(12, 30)),
        (LEC, time(12, 30), time(14)),
    )

    assert _consecutive(ConstraintEngine().evaluate(*three)) == []
    [conflict] = _consecutive(ConstraintEngine().evaluate(*four))
    assert len(conflict.class_ids) == 4
    assert conflict.resource_key == (str(FACULTY_1), "M")
    assert "4 lectures back to back on M 08:00-14:00" in conflict.message


def test_two_labs_in_a_row_are_allowed_and_a_third_is_not():
    two = _run_problem((LAB, time(8), time(10, 30)), (LAB, time(10, 30), time(13)))
    three = _run_problem(
        (LAB, time(8), time(10, 30)),
        (LAB, time(10, 30), time(13)),
        (LAB, time(13), time(15, 30)),
    )

    assert _consecutive(ConstraintEngine().evaluate(*two)) == []
    [conflict] = _consecutive(ConstraintEngine().evaluate(*three))
    assert "3 labs back to back" in conflict.message


def test_a_gap_breaks_the_run():
    problem = _run_problem(
        (LEC, time(8), time(9, 30)),
        (LEC, time(9, 30), time(11)),
        (LEC, time(11), time(12, 30)),
        (LEC, time(13), time(14, 30)),
    )

    assert _consecutive(ConstraintEngine().evaluate(*problem)) == []


def test_locked_classes_in_other_blocks_extend_the_run():
    problem, assignments = _run_problem(
        (LEC, time(8), time(9, 30)),
        (LEC, time(9, 30), time(11)),
        (LEC, time(11), time(12, 30)),
        (LEC, time(12, 30), time(14)),
    )
    # The fourth lecture is already placed in another Block.
    lock = LockedAssignment(
        class_id=UUID("00000000-0000-0000-0000-000000000099"),
        faculty_id=FACULTY_1,
        time_slot_id=assignments[3].time_slot_id,
        room_code="<ROOM-2>",
        meeting_days="M",
        block_id=UUID("00000000-0000-0000-0000-000000000088"),
    )

    report = ConstraintEngine().evaluate(
        problem, assignments[:3], locked_assignments=(lock,)
    )

    [conflict] = _consecutive(report)
    assert lock.class_id in conflict.class_ids
    assert "a class already placed in another Block" in conflict.message


def test_combined_blocks_in_one_session_count_once():
    problem, assignments = _run_problem(
        (LEC, time(8), time(9, 30)),
        (LEC, time(9, 30), time(11)),
        (LEC, time(11), time(12, 30)),
    )
    twin = replace(
        problem.classes[2],
        class_id=UUID("00000000-0000-0000-0000-000000000097"),
        block_id=UUID("00000000-0000-0000-0000-000000000096"),
        section_letter="B",
    )
    problem = replace(problem, classes=problem.classes + (twin,))
    assignments += (replace(assignments[2], class_id=twin.class_id),)

    assert _consecutive(ConstraintEngine().evaluate(problem, assignments)) == []
