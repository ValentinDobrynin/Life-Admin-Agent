"""r2_key to r2_keys in reference_data

Revision ID: 6c9d2e3f4a5b
Revises: 5b8c1d2e3f4a
Create Date: 2026-03-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c9d2e3f4a5b"
down_revision: str | Sequence[str] | None = "5b8c1d2e3f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add r2_keys JSON array column, migrate existing r2_key data, drop r2_key."""
    # 1. Add new column with default empty array
    op.add_column(
        "reference_data",
        sa.Column("r2_keys", sa.JSON(), nullable=False, server_default="[]"),
    )

    # 2. Migrate existing non-null r2_key values into the new array column
    op.execute(
        """
        UPDATE reference_data
        SET r2_keys = json_build_array(r2_key)
        WHERE r2_key IS NOT NULL
        """
    )

    # 3. Drop the old column
    op.drop_column("reference_data", "r2_key")


def downgrade() -> None:
    """Restore r2_key string column from first element of r2_keys."""
    op.add_column(
        "reference_data",
        sa.Column("r2_key", sa.String(length=500), nullable=True),
    )
    op.execute(
        """
        UPDATE reference_data
        SET r2_key = r2_keys->>0
        WHERE json_array_length(r2_keys) > 0
        """
    )
    op.drop_column("reference_data", "r2_keys")
