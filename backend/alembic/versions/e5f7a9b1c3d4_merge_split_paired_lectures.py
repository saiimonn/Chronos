"""Merge paired Blocks' lectures still held on two offerings.

Blocks linked before automatic lecture pairing kept one LEC on two groups
(e.g. BSCS 1A on CIS 1203 G1, 1B on G2), so the GA scheduled the joint lecture
as two unrelated classes with their own teacher, room and time. Cohort
generation and Block edits merge such pairs, but only when someone runs them;
this backfills every database once.

Mirrors `merge_paired_lectures`, as plain SQL so later model changes cannot
break it: within each non-archived term, the later letter of a pair (B of A-B,
D of C-D, ...) moves onto the earlier letter's offering for every LEC both
take. A class that already has schedule records, or a target offering already
serving two Blocks, is left as it is. An offering left serving nobody is
removed, and that subject's groups are renumbered 1..n unless one of them is
already scheduled.

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f7a9b1c3d4"
down_revision: str | None = "d4e6f8a0b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors SubjectType.LEC.max_blocks_per_offering.
_MAX_BLOCKS_PER_LECTURE = 2

# Each (first, second) link pair: the same LEC on two offerings, for a Block
# with an even letter index (A, C, E, ...) and the next letter in its cohort.
_SPLIT_LECTURES = sa.text(
    """
    SELECT second_link.class_id,
           first_link.offering_id AS target_offering_id,
           second_link.offering_id AS old_offering_id,
           second_link.term_id,
           second_link.subject_code
    FROM block_section AS first_block
    JOIN term ON term.term_id = first_block.term_id AND NOT term.is_archived
    JOIN block_section AS second_block
      ON second_block.term_id = first_block.term_id
     AND second_block.program_code = first_block.program_code
     AND second_block.year_level = first_block.year_level
     AND ascii(second_block.section_letter) = ascii(first_block.section_letter) + 1
    JOIN block_section_offering AS first_link
      ON first_link.block_id = first_block.block_id
    JOIN block_section_offering AS second_link
      ON second_link.block_id = second_block.block_id
     AND second_link.subject_code = first_link.subject_code
     AND second_link.offering_id <> first_link.offering_id
    JOIN subject ON subject.subject_code = second_link.subject_code
    WHERE subject.subject_type = 'LEC'
      AND first_block.section_letter ~ '^[A-Z]$'
      AND second_block.section_letter ~ '^[A-Z]$'
      AND (ascii(first_block.section_letter) - ascii('A')) % 2 = 0
    ORDER BY first_block.term_id, first_block.program_code,
             first_block.year_level, first_block.section_letter,
             second_link.subject_code
    """
)


def _count(connection: sa.Connection, sql: str, **parameters: object) -> int:
    return connection.execute(sa.text(sql), parameters).scalar_one()


def upgrade() -> None:
    connection = op.get_bind()
    emptied: set[tuple[object, str]] = set()
    for row in connection.execute(_SPLIT_LECTURES).mappings().all():
        if _count(
            connection,
            "SELECT count(*) FROM schedule_record WHERE class_id = :class_id",
            class_id=row["class_id"],
        ):
            continue
        if (
            _count(
                connection,
                "SELECT count(*) FROM block_section_offering "
                "WHERE offering_id = :offering_id",
                offering_id=row["target_offering_id"],
            )
            >= _MAX_BLOCKS_PER_LECTURE
        ):
            continue
        connection.execute(
            sa.text(
                "UPDATE block_section_offering SET offering_id = :target "
                "WHERE class_id = :class_id"
            ),
            {"target": row["target_offering_id"], "class_id": row["class_id"]},
        )
        if not _count(
            connection,
            "SELECT count(*) FROM block_section_offering "
            "WHERE offering_id = :offering_id",
            offering_id=row["old_offering_id"],
        ):
            connection.execute(
                sa.text("DELETE FROM subject_offering WHERE offering_id = :offering_id"),
                {"offering_id": row["old_offering_id"]},
            )
            emptied.add((row["term_id"], row["subject_code"]))

    # Close the gaps the removed groups left (G1, G3 -> G1, G2), as
    # `compact_group_numbers` does, unless a group is already scheduled.
    for term_id, subject_code in sorted(emptied, key=lambda key: (str(key[0]), key[1])):
        if _count(
            connection,
            "SELECT count(*) FROM schedule_record AS record "
            "JOIN block_section_offering AS link ON link.class_id = record.class_id "
            "WHERE link.term_id = :term_id AND link.subject_code = :subject_code",
            term_id=term_id,
            subject_code=subject_code,
        ):
            continue
        offerings = connection.execute(
            sa.text(
                "SELECT offering_id, group_no FROM subject_offering "
                "WHERE term_id = :term_id AND subject_code = :subject_code "
                "ORDER BY group_no"
            ),
            {"term_id": term_id, "subject_code": subject_code},
        ).all()
        # Ascending: each target number is at most the row's own and already
        # vacated, so the unique constraint never sees a duplicate.
        for group_no, (offering_id, current) in enumerate(offerings, start=1):
            if current != group_no:
                connection.execute(
                    sa.text(
                        "UPDATE subject_offering SET group_no = :group_no "
                        "WHERE offering_id = :offering_id"
                    ),
                    {"group_no": group_no, "offering_id": offering_id},
                )


def downgrade() -> None:
    # A data backfill: the split it removed was the defect, so there is
    # nothing to restore.
    pass
