"""The offering rules that do not need a database.

Block cardinality lives here ahead of the Block association table (Epic B):
the rule is a property of the subject type, so it can be settled and tested
now and wired to real Blocks when they exist.
"""

import pytest

from uuid import UUID

from app.domain import SubjectOfferingDTO, SubjectType


@pytest.mark.parametrize(
    ("subject_type", "expected"), [(SubjectType.LAB, 1), (SubjectType.LEC, 2)]
)
def test_max_blocks_per_offering_follows_subject_type(
    subject_type: SubjectType, expected: int
) -> None:
    assert subject_type.max_blocks_per_offering == expected


def test_group_label_reads_as_the_subject_code_and_group_number() -> None:
    offering = SubjectOfferingDTO(
        offering_id=UUID("00000000-0000-0000-0000-0000000000c1"),
        term_id=UUID("00000000-0000-0000-0000-0000000000c2"),
        subject_code="CIS 1101",
        group_no=1,
    )

    assert offering.group_label == "CIS 1101 G1"
