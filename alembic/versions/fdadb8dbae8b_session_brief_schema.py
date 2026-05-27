"""session brief schema

Revision ID: fdadb8dbae8b
Revises: d14bc789212f
Create Date: 2026-05-27 09:25:23.125764

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'fdadb8dbae8b'
down_revision: Union[str, Sequence[str], None] = 'd14bc789212f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # health_events
    op.create_table(
        'health_events',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('started_at', sa.Date(), nullable=True),
        sa.Column('expected_resolution', sa.Date(), nullable=True),
        sa.Column(
            'affects',
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "event_type IN ('dental_procedure','acute_infection','antibiotic_course',"
            "'fever','injury','scheduled_lab_draw','scheduled_dexa','scheduled_sleep_study')",
            name='health_events_event_type_check',
        ),
        sa.CheckConstraint(
            "status IN ('active','pending','resolving','resolved')",
            name='health_events_status_check',
        ),
    )
    op.create_index(
        'health_events_user_status_idx',
        'health_events',
        ['user_id', 'status'],
        unique=False,
    )
    op.create_index(
        'health_events_expected_idx',
        'health_events',
        ['expected_resolution'],
        unique=False,
        postgresql_where=sa.text("status IN ('pending','active','resolving')"),
    )

    # regulation_cache
    op.create_table(
        'regulation_cache',
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column(
            'brief_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'cached_at',
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column('latest_ingestion_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('latest_write_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'as_of_date'),
    )
    op.create_index(
        'regulation_cache_cached_at_idx',
        'regulation_cache',
        ['cached_at'],
        unique=False,
    )

    # manual_log subjective markers
    op.add_column(
        'manual_log',
        sa.Column('soreness_1_10', sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        'manual_log',
        sa.Column('sleep_subjective_1_10', sa.SmallInteger(), nullable=True),
    )

    # daily_metrics status columns (idempotent — columns already exist in prod).
    # Documented in PR body. Alembic doesn't emit IF NOT EXISTS natively for
    # add_column, so we use raw SQL.
    op.execute(
        "ALTER TABLE daily_metrics "
        "ADD COLUMN IF NOT EXISTS oura_status TEXT NOT NULL DEFAULT 'ok'"
    )
    op.execute(
        "ALTER TABLE daily_metrics "
        "ADD COLUMN IF NOT EXISTS whoop_status TEXT NOT NULL DEFAULT 'ok'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # daily_metrics ALTERs in upgrade() are idempotent no-ops on prod (columns
    # pre-existed our migration). Downgrade is intentionally a no-op so we
    # don't drop columns that other code depends on.

    op.drop_column('manual_log', 'sleep_subjective_1_10')
    op.drop_column('manual_log', 'soreness_1_10')

    op.drop_index('regulation_cache_cached_at_idx', table_name='regulation_cache')
    op.drop_table('regulation_cache')

    op.drop_index(
        'health_events_expected_idx',
        table_name='health_events',
        postgresql_where=sa.text("status IN ('pending','active','resolving')"),
    )
    op.drop_index('health_events_user_status_idx', table_name='health_events')
    op.drop_table('health_events')
