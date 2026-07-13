"""regulation_overrides table

Revision ID: 9abd8e07b339
Revises: fdadb8dbae8b
Create Date: 2026-07-13 12:59:46.750340

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9abd8e07b339"
down_revision: Union[str, Sequence[str], None] = "fdadb8dbae8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regulation_overrides",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "field IN ('kcal_target','training_modifier','state','add_override','remove_override')",
            name="regulation_overrides_field_check",
        ),
        sa.CheckConstraint(
            "created_by IN ('hugo','andrea','claude_chat','claude_code')",
            name="regulation_overrides_created_by_check",
        ),
    )
    op.create_index(
        "idx_reg_overrides_user_active",
        "regulation_overrides",
        ["user_id", "valid_from", "valid_until"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_reg_overrides_user_active", table_name="regulation_overrides")
    op.drop_table("regulation_overrides")
