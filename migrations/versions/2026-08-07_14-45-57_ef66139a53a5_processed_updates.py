"""processed updates

Revision ID: ef66139a53a5
Revises: 9a477d9ad3f5
Create Date: 2026-08-07 14:45:57.734195

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate emits `chain_health.db.types.UTCDateTime()` in the column
# definition but does not add this import — without it the migration dies with
# NameError on the very first run.
import chain_health.db.types

# revision identifiers, used by Alembic.
revision: str = "ef66139a53a5"
down_revision: str | Sequence[str] | None = "9a477d9ad3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "processed_updates",
        sa.Column(
            "id", sa.Integer(), autoincrement=False, nullable=False, comment="Telegram update id"
        ),
        sa.Column(
            "created_at",
            chain_health.db.types.UTCDateTime(),
            nullable=False,
            comment="UTC instant the update was applied",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processed_updates")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("processed_updates")
