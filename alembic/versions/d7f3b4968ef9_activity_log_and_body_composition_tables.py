"""activity_log and body_composition tables

Revision ID: d7f3b4968ef9
Revises: 9abd8e07b339
Create Date: 2026-07-15 11:32:50.080506

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f3b4968ef9'
down_revision: Union[str, Sequence[str], None] = '9abd8e07b339'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "activity_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False),
        sa.Column("distance_mi", sa.Numeric(6, 2), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("elevation_ft", sa.Integer(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("strain", sa.Numeric(4, 2), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "activity_type IN ('walk','run','ride','z2','hiit','strength','climb','other')",
            name="activity_log_type_check",
        ),
        sa.CheckConstraint(
            "source IN ('strava','whoop','peloton','manual','api')",
            name="activity_log_source_check",
        ),
    )
    op.create_index("idx_activity_log_user_date", "activity_log", ["user_id", "activity_date"])

    op.create_table(
        "body_composition",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("measured_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("weight_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("body_fat_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("lean_mass_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("fat_mass_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "source IN ('dexa','bioimpedance','hydrostatic','manual')",
            name="body_composition_source_check",
        ),
    )
    op.create_index("idx_body_comp_user_date", "body_composition", ["user_id", "measured_date"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_body_comp_user_date", table_name="body_composition")
    op.drop_table("body_composition")
    op.drop_index("idx_activity_log_user_date", table_name="activity_log")
    op.drop_table("activity_log")
