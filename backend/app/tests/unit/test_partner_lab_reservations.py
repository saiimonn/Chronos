"""Paired LABs placed together: the first Block's run reserves its partner's LABs.

A paired LAB must meet when its partner does, so whichever Block runs first
fixes the time for both. Its run places the partner's LABs too, returns them
as reservations, and the next runs hold them until the partner's own proposal
exists.
"""

from dataclasses import replace
from uuid import UUID

from app.domain import (
    BlockClassDTO,
    BlockGenerationContext,
    BlockSectionDTO,
    LockedAssignment,
    RoomDTO,
    RoomType,
    SubjectDTO,
    SubjectType,
)
from app.services.ga import GAConfig
from app.services.job_submission import add_proposal_locks, with_partner_lab_classes
from app.services.jobs import encode_block_ga_payload, run_ga
from app.tests.support.ga import GA_TERM_ID, make_ga_problem
from app.tests.unit.test_block_replacement import (
    CLASS_A1_ID,
    CLASS_B1_ID,
    FACULTY_1_ID,
    FACULTY_2_ID,
    TIME_SLOT_1_ID,
    TIME_SLOT_2_ID,
    _make_problem,
)
from app.tests.unit.test_block_review_edits import (
    _block_a_context,
    _context_job,
    _session_for,
)


def _reservation(class_id, faculty_id, room, days, slot):
    return {
        "class_id": str(class_id),
        "faculty_id": str(faculty_id),
        "room_code": room,
        "meeting_days": days,
        "time_slot_id": str(slot),
    }


def _held(context):
    return [(lock.class_id, lock.meeting_days) for lock in context.locked_assignments]


def test_a_partner_lab_reservation_is_held_until_its_block_has_a_proposal():
    # A run reserved Block B's LAB: the next Block must not take its teacher
    # or room, though Block B has no proposal of its own yet.
    job = _context_job()
    job.result = {
        "candidate": {"assignments": []},
        "partner_lab_reservations": [
            _reservation(CLASS_B1_ID, FACULTY_2_ID, "LB 102", "MW", TIME_SLOT_2_ID)
        ],
    }
    context = add_proposal_locks(
        _session_for(job), _block_a_context(), _make_problem(), [job.job_id]
    )
    assert _held(context) == [(CLASS_B1_ID, "MW")]


def test_a_blocks_own_proposal_wins_over_a_reservation_for_it():
    job = _context_job()  # Block B's own proposal: B1 on TTh.
    job.result["partner_lab_reservations"] = [
        _reservation(CLASS_B1_ID, FACULTY_2_ID, "LB 102", "MW", TIME_SLOT_2_ID)
    ]
    context = add_proposal_locks(
        _session_for(job), _block_a_context(), _make_problem(), [job.job_id]
    )
    assert _held(context) == [(CLASS_B1_ID, "TTh")]


def test_a_reservation_for_the_block_being_generated_is_not_a_lock():
    job = _context_job()
    job.result = {
        "candidate": {"assignments": []},
        "partner_lab_reservations": [
            _reservation(CLASS_A1_ID, FACULTY_1_ID, "LB 101", "MW", TIME_SLOT_1_ID)
        ],
    }
    context = add_proposal_locks(
        _session_for(job), _block_a_context(), _make_problem(), [job.job_id]
    )
    assert context.locked_assignments == ()


# ---- The run itself --------------------------------------------------------

_LAB_A = UUID(int=0x6101)
_LAB_B = UUID(int=0x6102)
_BLOCK_A = UUID(int=0x6201)
_BLOCK_B = UUID(int=0x6202)


def _lab(class_id, block_id, letter, offering):
    return BlockClassDTO(
        class_id=class_id, block_id=block_id, term_id=GA_TERM_ID,
        offering_id=UUID(int=offering), group_no=1, subject_code="LAB-1",
        program_code="<PROGRAM-1>", year_level=2, section_letter=letter, capacity=20,
    )


def _pair_context():
    base = make_ga_problem(include_unstaffable=False)
    lab_a = _lab(_LAB_A, _BLOCK_A, "A", 0x6301)
    lab_b = _lab(_LAB_B, _BLOCK_B, "B", 0x6302)
    faculty = tuple(replace(f, availability_mask="111") for f in base.faculty)
    rooms = tuple(
        RoomDTO(room_code=f"LAB-{n}", building_name="B", capacity=25,
                room_type=RoomType.LABORATORY, expanded=False)
        for n in (1, 2)
    )
    return BlockGenerationContext(
        term=base.term,
        selected_block_id=_BLOCK_A,
        selected_block=BlockSectionDTO(
            block_id=_BLOCK_A, term_id=GA_TERM_ID, program_code="<PROGRAM-1>",
            year_level=2, section_letter="A", capacity=20,
        ),
        selected_classes=(lab_a,),
        locked_assignments=(),
        faculty=faculty,
        subjects=(SubjectDTO(subject_code="LAB-1", subject_title="x", units=3,
                             subject_type=SubjectType.LAB),),
        rooms=rooms,
        programs=base.programs,
        time_slots=base.time_slots,
        base_schedule_id=None,
        lab_partners={_LAB_A: _LAB_B, _LAB_B: _LAB_A},
    ), (lab_a, lab_b)


def test_the_first_block_of_a_pair_places_both_labs_at_one_time():
    context, (_, lab_b) = _pair_context()
    problem = replace(_make_problem(), classes=(*context.selected_classes, lab_b))
    context = with_partner_lab_classes(context, problem)
    assert context.partner_lab_classes == (lab_b,)

    result = run_ga(encode_block_ga_payload(
        context, GAConfig(population_size=8, max_generations=5, tournament_size=2,
                          random_seed=3, meeting_day_patterns=("MW", "TTh")),
    ))

    (own,) = result["candidate"]["assignments"]
    (reserved,) = result["partner_lab_reservations"]
    assert own["class_id"] == str(_LAB_A)
    assert reserved["class_id"] == str(_LAB_B)
    assert (own["time_slot_id"], own["meeting_days"]) == (
        reserved["time_slot_id"], reserved["meeting_days"],
    )
    assert own["room_code"] != reserved["room_code"]
    assert own["faculty_id"] != reserved["faculty_id"]


def test_a_partner_already_held_is_not_placed_again():
    context, (_, lab_b) = _pair_context()
    problem = replace(_make_problem(), classes=(*context.selected_classes, lab_b))
    # Block B's LAB is already accepted or proposed: it pins the time instead.
    held = replace(context, locked_assignments=(
        LockedAssignment(
            class_id=_LAB_B, faculty_id=FACULTY_2_ID, time_slot_id=TIME_SLOT_1_ID,
            room_code="LAB-2", meeting_days="MW", block_id=_BLOCK_B,
            offering_id=UUID(int=0x6302), units=3,
        ),
    ))
    assert with_partner_lab_classes(held, problem).partner_lab_classes == ()
