"""v3 capture tables

Revision ID: 3ef8acaf4eec
Revises: c104f8c6d28d
Create Date: 2026-05-19 12:39:12.378153

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ef8acaf4eec'
down_revision: Union[str, Sequence[str], None] = 'c104f8c6d28d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'meals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('meal_date', sa.Date(), nullable=False),
        sa.Column('meal_time', sa.Time(), nullable=True),
        sa.Column('meal_name', sa.Text(), nullable=True),
        sa.Column('kcal', sa.Integer(), nullable=True),
        sa.Column('protein_g', sa.Integer(), nullable=True),
        sa.Column('fat_g', sa.Integer(), nullable=True),
        sa.Column('carbs_g', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('photo_path', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_meals_user_date', 'meals', ['user_id', 'meal_date'], unique=False)

    op.create_table(
        'workout_sets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('workout_id', sa.BigInteger(), nullable=False),
        sa.Column('set_number', sa.Integer(), nullable=False),
        sa.Column('exercise', sa.Text(), nullable=False),
        sa.Column('reps', sa.Integer(), nullable=False),
        sa.Column('weight_lbs', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('rpe', sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.ForeignKeyConstraint(['workout_id'], ['workouts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_workout_sets_workout', 'workout_sets', ['workout_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_workout_sets_workout', table_name='workout_sets')
    op.drop_table('workout_sets')
    op.drop_index('idx_meals_user_date', table_name='meals')
    op.drop_table('meals')
