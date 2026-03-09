"""add owner_ref_id and relation to reference_data

Revision ID: 5b8c1d2e3f4a
Revises: 3f8a1b2c4d5e
Create Date: 2026-03-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b8c1d2e3f4a"
down_revision: str | Sequence[str] | None = "3f8a1b2c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "reference_data",
        sa.Column("owner_ref_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "reference_data",
        sa.Column("relation", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("reference_data", "relation")
    op.drop_column("reference_data", "owner_ref_id")
