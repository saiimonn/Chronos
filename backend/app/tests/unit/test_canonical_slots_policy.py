from datetime import time
from uuid import UUID, uuid4

from app.alembic_support.canonical_slots import (
    CANONICAL_WINDOWS,
    MASK_LENGTH,
    label,
    slot_id,
)


def test_the_frozen_table_is_the_approved_grid():
    assert len(CANONICAL_WINDOWS) == 48
    assert MASK_LENGTH == 48
    assert CANONICAL_WINDOWS[0] == ("LEC", time(7, 30), time(9, 0), 0)
    assert CANONICAL_WINDOWS[24] == ("LEC", time(19, 30), time(21, 0), 24)
    assert CANONICAL_WINDOWS[25] == ("LAB", time(7, 30), time(10, 0), 25)
    assert CANONICAL_WINDOWS[47] == ("LAB", time(18, 30), time(21, 0), 47)


def test_no_frozen_window_escapes_the_day():
    assert all(start >= time(7, 30) for _, start, _, _ in CANONICAL_WINDOWS)
    assert all(end <= time(21, 0) for _, _, end, _ in CANONICAL_WINDOWS)


def test_the_frozen_identity_matches_the_domain_module():
    """The migration and the service must agree, or a re-grid would duplicate rows."""
    from app.domain import generate_windows

    term_id = uuid4()
    frozen = [
        (subject_type, start, end, ordinal, slot_id(term_id, subject_type, start))
        for subject_type, start, end, ordinal in CANONICAL_WINDOWS
    ]
    live = [
        (
            window.subject_type.value,
            window.start_time,
            window.end_time,
            window.ordinal,
            window.slot_id(term_id),
        )
        for window in generate_windows()
    ]

    assert frozen == live


def test_labels_name_the_window():
    assert label("LEC", time(7, 30), time(9, 0)) == "LEC 07:30-09:00"
    assert label("LAB", time(18, 30), time(21, 0)) == "LAB 18:30-21:00"


def test_slot_ids_are_stable_and_term_scoped():
    term_id = uuid4()
    other = uuid4()

    assert slot_id(term_id, "LEC", time(7, 30)) == slot_id(term_id, "LEC", time(7, 30))
    assert isinstance(slot_id(term_id, "LEC", time(7, 30)), UUID)
    assert slot_id(term_id, "LEC", time(7, 30)) != slot_id(other, "LEC", time(7, 30))
    assert slot_id(term_id, "LEC", time(7, 30)) != slot_id(term_id, "LAB", time(7, 30))


def test_the_mask_length_matches_the_grid():
    """A reset mask has one character per window, or the codec rejects it."""
    assert MASK_LENGTH == len(CANONICAL_WINDOWS)
    assert len("0" * MASK_LENGTH) == 48
