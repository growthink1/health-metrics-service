"""v4 goals tables

Revision ID: d14bc789212f
Revises: 3ef8acaf4eec
Create Date: 2026-05-20 15:02:34.122944

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd14bc789212f'
down_revision: Union[str, Sequence[str], None] = '3ef8acaf4eec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'goals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('goal_type', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('metric', sa.Text(), nullable=False),
        sa.Column('metric_params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('start_value', sa.Numeric(), nullable=True),
        sa.Column('target_value', sa.Numeric(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_goals_user_active', 'goals', ['user_id'], unique=False,
                    postgresql_where=sa.text("status='active'"))

    op.create_table(
        'milestones',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('goal_id', sa.BigInteger(), nullable=False),
        sa.Column('target_value', sa.Numeric(), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('hit_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('hit_value', sa.Numeric(), nullable=True),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_milestones_goal', 'milestones', ['goal_id'], unique=False)

    op.create_table(
        'subgoals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('goal_id', sa.BigInteger(), nullable=False),
        sa.Column('preset', sa.Text(), nullable=False),
        sa.Column('target_value', sa.Numeric(), nullable=False),
        sa.Column('window_days', sa.Integer(), server_default=sa.text('7'), nullable=False),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'goal_recommendations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('goal_id', sa.BigInteger(), nullable=False),
        sa.Column('rec_date', sa.Date(), nullable=False),
        sa.Column('trajectory', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('actions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('narration', sa.Text(), nullable=False),
        sa.Column('signals_hash', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('goal_id', 'rec_date', name='uq_goal_recommendations_goal_date'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('goal_recommendations')
    op.drop_table('subgoals')
    op.drop_index('idx_milestones_goal', table_name='milestones')
    op.drop_table('milestones')
    op.drop_index('idx_goals_user_active', table_name='goals')
    op.drop_table('goals')
