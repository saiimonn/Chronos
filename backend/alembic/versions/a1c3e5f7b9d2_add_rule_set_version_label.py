"""Add rule_set.version_label: an optional display name such as "1.1".

Lets a version re-publish an earlier version's exact rules (for example with
per-teacher coverage attached) under a name that says so, while ``version``
stays the monotonic integer the GA and the audit trail refer to.

Revision ID: a1c3e5f7b9d2
Revises: b8d2f4a6c0e1
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c3e5f7b9d2"
down_revision = "b8d2f4a6c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_set", sa.Column("version_label", sa.String(20), nullable=True)
    )
    op.create_unique_constraint(
        "rule_set_version_label_key", "rule_set", ["version_label"]
    )


def downgrade() -> None:
    op.drop_constraint("rule_set_version_label_key", "rule_set", type_="unique")
    op.drop_column("rule_set", "version_label")
