from datetime import time
from uuid import UUID, uuid4

import pytest

from app.domain import TimeSlotDTO
from app.services.validation.availability import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
)

TERM_ID = UUID("00000000-0000-0000-0000-0000000000b1")


def slot(ordinal: int) -> TimeSlotDTO:
    return TimeSlotDTO(
        time_slot_id=UUID(int=ordinal + 1),
        term_id=TERM_ID,
        label=f"slot-{ordinal}",
        start_time=time(8 + ordinal),
        end_time=time(9 + ordinal),
        ordinal=ordinal,
    )


def test_encode_sets_a_bit_per_selected_slot_in_ordinal_order():
    codec = AvailabilityMaskCodec()
    slots = [slot(2), slot(0), slot(1)]

    mask = codec.encode({slot(0).time_slot_id, slot(2).time_slot_id}, slots)

    assert mask == "101"


def test_encode_of_an_empty_selection_is_all_zeroes():
    assert AvailabilityMaskCodec().encode(set(), [slot(0), slot(1)]) == "00"


def test_encode_then_decode_round_trips():
    codec = AvailabilityMaskCodec()
    slots = [slot(0), slot(1), slot(2)]
    selected = {slot(1).time_slot_id}

    assert codec.available_slot_ids(codec.encode(selected, slots), slots) == selected


def test_encode_rejects_an_identifier_outside_the_grid():
    codec = AvailabilityMaskCodec()

    with pytest.raises(AvailabilityMaskError):
        codec.encode({uuid4()}, [slot(0)])


def test_encode_rejects_duplicate_ordinals():
    codec = AvailabilityMaskCodec()
    duplicated = TimeSlotDTO(
        time_slot_id=uuid4(),
        term_id=TERM_ID,
        label="clash",
        start_time=time(8),
        end_time=time(9),
        ordinal=0,
    )

    with pytest.raises(AvailabilityMaskError):
        codec.encode(set(), [slot(0), duplicated])


def test_decode_still_rejects_duplicate_ordinals():
    codec = AvailabilityMaskCodec()
    duplicated = TimeSlotDTO(
        time_slot_id=uuid4(),
        term_id=TERM_ID,
        label="clash",
        start_time=time(8),
        end_time=time(9),
        ordinal=0,
    )

    with pytest.raises(AvailabilityMaskError):
        codec.available_slot_ids("10", [slot(0), duplicated])


def test_an_unset_mask_is_open_in_every_slot():
    slots = [slot(1), slot(0), slot(2)]

    available = AvailabilityMaskCodec().available_slot_ids("", slots)

    assert available == {s.time_slot_id for s in slots}


def test_a_saved_empty_selection_stays_closed_in_every_slot():
    codec = AvailabilityMaskCodec()
    slots = [slot(0), slot(1)]

    assert codec.available_slot_ids(codec.encode(set(), slots), slots) == frozenset()
