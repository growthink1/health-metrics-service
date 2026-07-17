"""regulation_cache brief_schema_version column

Revision ID: e2e7f619807b
Revises: 9abd8e07b339
Create Date: 2026-07-13 15:02:55.500780

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2e7f619807b'
down_revision: Union[str, Sequence[str], None] = '9abd8e07b339'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Adds regulation_cache.brief_schema_version. Existing rows (written before
    this column) get server_default '0', which the freshness check in
    regulation/cache.py treats as stale against any real BRIEF_SCHEMA_VERSION --
    so they recompute on the next read. IF NOT EXISTS keeps it idempotent.
    """
    op.execute(
        "ALTER TABLE regulation_cache "
        "ADD COLUMN IF NOT EXISTS brief_schema_version TEXT NOT NULL DEFAULT '0'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE regulation_cache DROP COLUMN IF EXISTS brief_schema_version")
