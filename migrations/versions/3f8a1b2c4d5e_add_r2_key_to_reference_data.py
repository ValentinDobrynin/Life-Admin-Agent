"""add r2_key to reference_data

Revision ID: 3f8a1b2c4d5e
Revises: 109691c02427
Create Date: 2026-03-07 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f8a1b2c4d5e"
down_revision: str | Sequence[str] | None = "109691c02427"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "reference_data",
        sa.Column("r2_key", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("reference_data", "r2_key")
