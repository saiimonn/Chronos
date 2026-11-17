"""Term-dependent day patterns and Friday-only double sessions.

Terms 1-2 meet MW, TTh or F (Friday-only: both weekly sessions back to back,
so twice the session length); term 3, the summer term, meets MTWTh.
"""

from dataclasses import replace
from datetime import time
from random import Random
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domain import (
    ConstraintLimits,
    Assignment,
    BlockClassDTO,
    CandidateSchedule,
    ConflictCode,
    CourseProgramDTO,
    FacultyDTO,
    RoomDTO,
    RoomType,
    SchedulingProblem,
    SubjectDTO,
    SubjectType,
    TermDTO,
    TimeSlotDTO,
    build_offering_block_ids,
)
from app.domain.meeting_patterns import (
    allowed_meeting_patterns,
    continuation_slots,
    decode_preferred_patterns,
    encode_preferred_patterns,
    first_choice_patterns,
    meeting_end,
    meeting_label,
    term_meeting_patterns,
)
from app.schemas.catalog import SubjectCreate, SubjectUpdate
from app.services.ga.config import GAConfig, validate_meeting_day_patterns

from app.services.ga.operators import GeneSampler
from app.services.ga.rule_scoring import RuleBackedHistoricalScorer
from app.services.jobs.codecs import (
    decode_scheduling_problem,
    encode_scheduling_problem,
)
from app.services.validation import ScheduleValidator

TERM_ID = UUID("00000000-0000-0000-0000-0000000000a1")
FACULTY_ID = UUID("00000000-0000-0000-0000-0000000000a2")
OTHER_FACULTY_ID = UUID("00000000-0000-0000-0000-0000000000a3")
CLASS_ID = UUID("00000000-0000-0000-0000-0000000000a4")
OTHER_CLASS_ID = UUID("00000000-0000-0000-0000-0000000000a5")
# Lifts the 8-hour Block day and 6-hour run limit so the grid stays whole.
WHOLE_DAY = ConstraintLimits(
    block_day_target_hours=24, block_day_limit_hours=24, back_to_back_limit_hours=24
)


SLOT_0730 = UUID("00000000-0000-0000-0000-0000000000b1")
SLOT_0900 = UUID("00000000-0000-0000-0000-0000000000b2")
SLOT_1930 = UUID("00000000-0000-0000-0000-0000000000b3")


def _slot(slot_id: UUID, start: time, end: time, ordinal: int) -> TimeSlotDTO:
    return TimeSlotDTO(
        time_slot_id=slot_id,
        term_id=TERM_ID,
        label=f"LEC {start:%H:%M}-{end:%H:%M}",
        start_time=start,
        end_time=end,
        ordinal=ordinal,
    )


SLOTS = (
    _slot(SLOT_0730, time(7, 30), time(9, 0), 0),
    _slot(SLOT_0900, time(9, 0), time(10, 30), 1),
    _slot(SLOT_1930, time(19, 30), time(21, 0), 2),
)


def _class(class_id: UUID, block: str, letter: str) -> BlockClassDTO:
    return BlockClassDTO(
        class_id=class_id,
        block_id=UUID(int=int(block, 16)),
        term_id=TERM_ID,
        offering_id=UUID(int=class_id.int + 1000),
        group_no=1,
        subject_code="CIS 1102N",
        program_code="BSIT",
        year_level=1,
        section_letter=letter,
        capacity=20,
    )


def _problem(term_number: int = 1, mask: str = "111") -> SchedulingProblem:
    classes = (_class(CLASS_ID, "c1", "A"), _class(OTHER_CLASS_ID, "c2", "B"))
    return SchedulingProblem(
        term=TermDTO(
            term_id=TERM_ID,
            academic_year="2026-2027",
            term_number=term_number,
            is_active=True,
        ),
        faculty=(
            FacultyDTO(
                faculty_id=FACULTY_ID,
                full_name="Teacher One",
                is_full_time=True,
                availability_mask=mask,
            ),
            FacultyDTO(
                faculty_id=OTHER_FACULTY_ID,
                full_name="Teacher Two",
                is_full_time=True,
                availability_mask="111",
            ),
        ),
        subjects=(
            SubjectDTO(
                subject_code="CIS 1102N",
                subject_title="INTRODUCTION TO COMPUTING",
                units=3,
                subject_type=SubjectType.LEC,
            ),
        ),
        rooms=(
            RoomDTO(
                room_code="LB 101",
                building_name="LB",
                capacity=40,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
            RoomDTO(
                room_code="LB 102",
                building_name="LB",
                capacity=40,
                room_type=RoomType.LECTURE,
                expanded=False,
            ),
        ),
        programs=(CourseProgramDTO(program_code="BSIT", program_name="BSIT"),),
        classes=classes,
        time_slots=SLOTS,
        offering_block_ids=build_offering_block_ids(classes),
    )


def _candidate(*assignments: Assignment) -> CandidateSchedule:
    return CandidateSchedule(
        schedule_id=UUID(int=99), term_id=TERM_ID, assignments=assignments
    )


def _assign(
    class_id: UUID,
    days: str,
    slot_id: UUID = SLOT_0730,
    faculty_id: UUID = FACULTY_ID,
    room: str = "LB 101",
) -> Assignment:
    return Assignment(
        class_id=class_id,
        faculty_id=faculty_id,
        time_slot_id=slot_id,
        room_code=room,
        meeting_days=days,
    )


def _codes(problem, candidate, *, strict=True) -> list[str]:
    result = ScheduleValidator().validate(
        problem, candidate, require_allowed_patterns=strict
    )
    return [conflict.code for conflict in result.constraint_report.conflicts]


# --- Domain rules ---------------------------------------------------------


def test_semesters_allow_mw_tth_f_and_summer_allows_mtwth():
    # FSa, Sa and Su (weekend) are allowed on edit in a semester.
    assert allowed_meeting_patterns(1) == ("MW", "TTh", "F", "FSa", "Sa", "Su")
    assert allowed_meeting_patterns(2) == ("MW", "TTh", "F", "FSa", "Sa", "Su")
    # The summer term allows only Friday-only besides MTWTh, set by hand.
    assert allowed_meeting_patterns(3) == ("MTWTh", "F")


def test_the_ga_never_draws_a_weekend_pattern():
    assert term_meeting_patterns(1, ("MW", "Su")) == ("MW",)
    assert term_meeting_patterns(3, ("Sa",)) == ("MTWTh",)


def test_summer_ignores_configuration_and_semesters_filter_it():
    assert term_meeting_patterns(3, ("MW", "TTh")) == ("MTWTh",)
    assert term_meeting_patterns(1, ("TTh", "F")) == ("TTh", "F")


def test_friday_only_lecture_runs_three_hours_and_lab_five():
    lecture = SLOTS[0]
    assert meeting_end(lecture, "F") == time(10, 30)
    assert meeting_end(lecture, "MW") == time(9, 0)
    assert meeting_label(lecture, "F") == "LEC 07:30-10:30"
    lab = replace(lecture, label="LAB 07:30-10:00", end_time=time(10, 0))
    assert meeting_end(lab, "F") == time(12, 30)


def test_saturday_only_runs_both_sessions_and_friday_saturday_splits_them():
    lecture = SLOTS[0]
    lab = replace(lecture, label="LAB 07:30-10:00", end_time=time(10, 0))
    # Saturday-only carries the whole week in one day: 3h lecture, 5h lab.
    assert meeting_end(lecture, "Sa") == time(10, 30)
    assert meeting_end(lab, "Sa") == time(12, 30)
    assert continuation_slots(lecture, "Sa", SLOTS) == (SLOTS[1],)
    # Friday-Saturday splits it: one normal session (1h30 / 2h30) each day.
    assert meeting_end(lecture, "FSa") == time(9, 0)
    assert meeting_end(lab, "FSa") == time(10, 0)
    assert continuation_slots(lecture, "FSa", SLOTS) == ()


def test_friday_only_second_session_is_the_following_slot():
    assert continuation_slots(SLOTS[0], "F", SLOTS) == (SLOTS[1],)
    assert continuation_slots(SLOTS[0], "MW", SLOTS) == ()
    # Nothing starts at 21:00, the end of the teaching day.
    assert continuation_slots(SLOTS[2], "F", SLOTS) is None


# --- GA configuration ------------------------------------------------------


def test_configuration_accepts_semester_patterns():
    assert validate_meeting_day_patterns(("MW", "TTh", "F")) == ("MW", "TTh", "F")


@pytest.mark.parametrize("pattern", ["MWTTh", "MTWTh", "MWF", "M", "Sa", "WM"])
def test_configuration_rejects_every_other_pattern(pattern):
    with pytest.raises(ValueError, match="MW, TTh or F"):
        validate_meeting_day_patterns(("MW", pattern))


# --- Validator -------------------------------------------------------------


def test_semester_class_on_four_days_is_flagged():
    candidate = _candidate(_assign(CLASS_ID, "MTWTh"))
    assert ConflictCode.INVALID_MEETING_PATTERN in _codes(_problem(1), candidate)


def test_summer_class_must_meet_mtwth():
    assert _codes(_problem(3), _candidate(_assign(CLASS_ID, "MTWTh"))) == []
    assert ConflictCode.INVALID_MEETING_PATTERN in _codes(
        _problem(3), _candidate(_assign(CLASS_ID, "MW"))
    )


def test_pattern_check_is_opt_in_for_historical_data():
    candidate = _candidate(_assign(CLASS_ID, "MWF"))
    assert ConflictCode.INVALID_MEETING_PATTERN not in _codes(
        _problem(1), candidate, strict=False
    )


def test_friday_only_class_occupies_both_sessions():
    # A 07:30 Friday-only class runs to 10:30, so it collides with the same
    # teacher's Friday-only class at 09:00 but not with one on MW at 09:00.
    clash = _candidate(
        _assign(CLASS_ID, "F", SLOT_0730),
        _assign(OTHER_CLASS_ID, "F", SLOT_0900, room="LB 102"),
    )
    assert ConflictCode.FACULTY_OVERLAP in _codes(_problem(1), clash)
    apart = _candidate(
        _assign(CLASS_ID, "F", SLOT_0730),
        _assign(OTHER_CLASS_ID, "MW", SLOT_0900, room="LB 102"),
    )
    assert _codes(_problem(1), apart) == []


def test_friday_only_needs_the_teacher_free_for_the_second_session():
    # Available 07:30-09:00 only: the second session at 09:00 is not free.
    candidate = _candidate(_assign(CLASS_ID, "F", SLOT_0730))
    assert ConflictCode.FACULTY_UNAVAILABLE in _codes(_problem(1, mask="101"), candidate)
    assert _codes(_problem(1, mask="110"), candidate) == []


def test_friday_only_class_may_not_run_past_closing():
    candidate = _candidate(_assign(CLASS_ID, "F", SLOT_1930))
    assert ConflictCode.OUTSIDE_TEACHING_CALENDAR in _codes(_problem(1), candidate)


# --- GA sampler -------------------------------------------------------------


def _config(patterns: tuple[str, ...]) -> GAConfig:
    return GAConfig(
        population_size=2, tournament_size=2, elite_count=0, meeting_day_patterns=patterns
    )


def test_summer_term_samples_only_mtwth():
    sampler = GeneSampler(_problem(3), _config(("MW", "TTh")))
    for offering_id in sampler.offering_ids:
        assert sampler.meeting_day_patterns(offering_id) == ("MTWTh",)
    genes = sampler.sample_individual(Random(1))
    assert {gene.meeting_days for gene in genes} == {"MTWTh"}


def test_friday_only_genes_start_where_the_second_session_fits():
    sampler = GeneSampler(replace(_problem(1), limits=WHOLE_DAY), _config(("F",)))
    offering_id = sampler.offering_ids[0]
    assert sampler.time_slot_pool(offering_id, FACULTY_ID, "F") == (SLOT_0730,)
    assert sampler.time_slot_pool(offering_id, FACULTY_ID, "MW") == (
        SLOT_0730,
        SLOT_0900,
        SLOT_1930,
    )
    rng = Random(3)
    for _ in range(20):
        gene = sampler.sample_gene(offering_id, rng)
        if gene.faculty_id == FACULTY_ID:
            assert (gene.meeting_days, gene.time_slot_id) == ("F", SLOT_0730)


# --- Subject day preferences -------------------------------------------------


def _prefer(problem: SchedulingProblem, *patterns: str) -> SchedulingProblem:
    subject = replace(problem.subjects[0], preferred_patterns=patterns)
    return replace(problem, subjects=(subject,))


def test_preferences_are_stored_in_a_fixed_order():
    assert encode_preferred_patterns(["F", "MW", "F"]) == "MW,F"
    assert decode_preferred_patterns("MW,F") == ("MW", "F")
    assert decode_preferred_patterns("") == ()
    assert decode_preferred_patterns("MWF,F") == ("F",)
    assert encode_preferred_patterns(["Su", "FSa", "Sa"]) == "FSa,Sa,Su"


def test_subject_api_accepts_only_semester_patterns():
    created = SubjectCreate(
        subject_code="X",
        subject_title="X",
        units=3,
        subject_type="LEC",
        preferred_day_patterns=["F", "TTh"],
    )
    assert created.preferred_day_patterns == ["TTh", "F"]
    assert SubjectUpdate(preferred_day_patterns=[]).preferred_day_patterns == []
    with pytest.raises(ValueError):
        SubjectUpdate(preferred_day_patterns=["MTWTh"])
    assert SubjectUpdate(
        allowed_room_codes=[" LB 470 ", "LB 461", "LB 470", ""]
    ).allowed_room_codes == ["LB 461", "LB 470"]


def test_weekend_pattern_is_drawn_only_for_a_subject_that_prefers_it():
    config = _config(("MW", "TTh", "F"))
    plain = GeneSampler(replace(_problem(1), limits=WHOLE_DAY), config)
    assert all(
        "FSa" not in plain.meeting_day_patterns(o) for o in plain.offering_ids
    )
    saturday = GeneSampler(replace(_prefer(_problem(1), "Sa"), limits=WHOLE_DAY), config)
    rng = Random(4)
    for offering_id in saturday.offering_ids:
        assert saturday.day_pattern_passes(offering_id) == (
            ("Sa",),
            ("MW", "TTh", "F"),
        )
        for _ in range(20):
            assert saturday.sample_gene(offering_id, rng).meeting_days == "Sa"
    # Summer packs every class into MTWTh, preference or not.
    summer = GeneSampler(replace(_prefer(_problem(3), "FSa"), limits=WHOLE_DAY), config)
    for offering_id in summer.offering_ids:
        assert summer.meeting_day_patterns(offering_id) == ("MTWTh",)


def test_fsa_meets_friday_and_saturday():
    candidate = _candidate(_assign(CLASS_ID, "FSa"))
    assert _codes(_problem(1), candidate) == []


def test_allowed_rooms_restrict_the_room_pool():
    problem = _problem(1)
    every = GeneSampler(problem, _config(("MW", "TTh")))
    offering_id = every.offering_ids[0]
    rooms = every.room_pool(offering_id)
    assert len(rooms) > 1
    subject = replace(problem.subjects[0], allowed_rooms=frozenset({rooms[-1]}))
    only = GeneSampler(replace(problem, subjects=(subject,)), _config(("MW", "TTh")))
    assert only.room_pool(offering_id) == (rooms[-1],)
    payload = encode_scheduling_problem(replace(problem, subjects=(subject,)))
    assert decode_scheduling_problem(payload).subjects[0].allowed_rooms == {rooms[-1]}


def test_first_choice_is_the_preference_else_the_two_day_patterns():
    semester = ("MW", "TTh", "F")
    assert first_choice_patterns(semester, ("F",)) == ("F",)
    assert first_choice_patterns(semester, ("TTh", "F")) == ("TTh", "F")
    assert first_choice_patterns(semester, ()) == ("MW", "TTh")
    # A preference the configuration does not enable is ignored.
    assert first_choice_patterns(("MW", "TTh"), ("F",)) == ("MW", "TTh")
    assert first_choice_patterns(("MTWTh",), ("F",)) == ("MTWTh",)


def test_friday_goes_only_to_subjects_that_prefer_it():
    config = _config(("MW", "TTh", "F"))
    plain = GeneSampler(replace(_problem(1), limits=WHOLE_DAY), config)
    rng = Random(5)
    for offering_id in plain.offering_ids:
        for _ in range(30):
            assert plain.sample_gene(offering_id, rng).meeting_days in ("MW", "TTh")

    friday = GeneSampler(replace(_prefer(_problem(1), "F"), limits=WHOLE_DAY), config)
    for offering_id in friday.offering_ids:
        for _ in range(30):
            gene = friday.sample_gene(offering_id, rng)
            assert gene.meeting_days == "F"
            assert gene.time_slot_id == SLOT_0730


def test_friday_preference_falls_back_when_friday_does_not_fit():
    # The only teacher is free at 07:30 and 19:30 but not 09:00, so no
    # Friday double session fits anywhere.
    problem = _prefer(_problem(1, mask="101"), "F")
    problem = replace(problem, faculty=problem.faculty[:1])
    sampler = GeneSampler(problem, _config(("MW", "TTh", "F")))
    offering_id = sampler.offering_ids[0]
    assert sampler.day_pattern_passes(offering_id) == (("F",), ("MW", "TTh"))
    gene = sampler.sample_gene(offering_id, Random(2))
    assert gene.is_resolved
    assert gene.meeting_days in ("MW", "TTh")


def test_payload_keeps_preferences_and_omits_them_when_unset():
    plain = encode_scheduling_problem(_problem(1))
    assert "preferred_patterns" not in plain["subjects"][0]
    preferred = encode_scheduling_problem(_prefer(_problem(1), "F"))
    decoded = decode_scheduling_problem(preferred)
    assert decoded.subjects[0].preferred_patterns == ("F",)


class _Guidance:
    def __init__(self):
        self.asked = []

    def rules_for(self, stream, faculty_id):
        return (SimpleNamespace(subject_code="CIS 1102N"),)

    def score_for(self, stream, faculty_id, **values):
        self.asked.append(values)
        return None


def test_friday_only_time_is_scored_on_its_whole_window():
    guidance = _Guidance()
    RuleBackedHistoricalScorer(guidance).score(
        _problem(1), [_assign(CLASS_ID, "F", SLOT_0730)]
    )
    assert {"time_slot": "LEC 07:30-10:30"} in guidance.asked
