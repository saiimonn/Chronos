"""CHR-132 / CHR-TERM-009 record which term each faculty availability was reviewed for.

SRS 4.6.2 asks scheduling personnel to confirm or update every faculty
member's availability_mask when preparing a new term, before generation. The
mask stays on Faculty (FR-AVL-002, no separate availability table); this column
only remembers the term the current mask was last confirmed or saved for, so
selecting another term leaves every member unreviewed until someone looks.

Existing rows start unreviewed: nobody has confirmed them against any term.

Revision ID: c7e3a9d1f5b2
Revises: a4d8f2c6e0b9
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7e3a9d1f5b2"
down_revision = "a4d8f2c6e0b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faculty",
        sa.Column(
            "availability_reviewed_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "term.term_id",
                name="fk_faculty_availability_reviewed_term",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_faculty_availability_reviewed_term", "faculty", type_="foreignkey"
    )
    op.drop_column("faculty", "availability_reviewed_term_id")
