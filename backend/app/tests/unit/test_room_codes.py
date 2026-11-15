import pytest

from app.domain import canonical_room_code


@pytest.mark.parametrize(
    ("building_code", "room_code", "expected"),
    [
        ("LB", "LB 486", "LB486"),
        ("LB", "LB486", "LB486"),
        ("LB", "lb486", "LB486"),
        ("LB", "LB-486", "LB486"),
        ("LB", "LB LB486", "LB486"),
        ("LB", "LB LB 486", "LB486"),
        ("LB", "  LB   4 8 6B ", "LB486B"),
        ("lb", "LB486", "LB486"),
        # Not "<building code> + number": trimmed and uppercased, never guessed.
        ("LB", "API-101", "API-101"),
        ("LB", "LBA", "LBA"),
        ("LB", "486", "486"),
        ("SH", "LB486", "LB486"),
        ("", "LB486", "LB486"),
    ],
)
def test_canonical_room_code(building_code: str, room_code: str, expected: str):
    assert canonical_room_code(building_code, room_code) == expected
