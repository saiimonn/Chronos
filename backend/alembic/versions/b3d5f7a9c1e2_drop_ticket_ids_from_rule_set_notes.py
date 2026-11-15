"""Drop the internal ticket IDs from imported RuleSet notes.

Offline imports used to default their notes to
"CHR-100 / CHR-COLAB-004 offline import; ...", which the Rule Sets page shows
verbatim. The ticket IDs mean nothing to the department, so the prefix becomes
"Offline import; ...". The artifact hash is kept.

Revision ID: b3d5f7a9c1e2
Revises: b9d1f3a5c7e2
"""

from alembic import op

revision = "b3d5f7a9c1e2"
down_revision = "b9d1f3a5c7e2"
branch_labels = None
depends_on = None

OLD = "CHR-100 / CHR-COLAB-004 offline import"
NEW = "Offline import"


def upgrade() -> None:
    op.execute(
        f"UPDATE rule_set SET notes = '{NEW}' || substr(notes, {len(OLD) + 1}) "
        f"WHERE notes LIKE '{OLD}%'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE rule_set SET notes = '{OLD}' || substr(notes, {len(NEW) + 1}) "
        f"WHERE notes LIKE '{NEW}; artifact sha256=%'"
    )
