"""legacy terminus — bridge from the old (pre-rebuild) migration chain.

Deployed Postgres databases that ran the old migrations have
``alembic_version = '6c9d2e3f4a5b'`` (the last revision of the old chain
``r2_key_to_r2_keys_in_reference_data``). After the rebuild we keep this
revision id as a no-op stub so alembic can still resolve it; the
following migration ``0001_initial`` drops the old tables and creates
the new schema.

For brand-new databases this stub runs first and does nothing, which
is also fine.

Revision ID: 6c9d2e3f4a5b
Revises:
Create Date: 2026-04-26
"""

from __future__ import annotations

revision = "6c9d2e3f4a5b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op. The migration only exists so alembic can resolve the legacy id."""
    pass


def downgrade() -> None:
    pass
