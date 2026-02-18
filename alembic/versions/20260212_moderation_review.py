"""Add moderation_review table for AI moderation

Revision ID: 20260212_mod
Revises: 20260212_deal
Create Date: 2026-02-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260212_mod"
down_revision: Union[str, None] = "20260212_deal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "moderation_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_moderation_entity"),
    )
    op.create_index("ix_moderation_review_id", "moderation_review", ["id"], unique=False)
    op.create_index("ix_moderation_review_entity_type", "moderation_review", ["entity_type"], unique=False)
    op.create_index("ix_moderation_review_entity_id", "moderation_review", ["entity_id"], unique=False)
    op.create_index("ix_moderation_review_risk_level", "moderation_review", ["risk_level"], unique=False)
    op.create_index("ix_moderation_review_updated_at", "moderation_review", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_moderation_review_updated_at", table_name="moderation_review")
    op.drop_index("ix_moderation_review_risk_level", table_name="moderation_review")
    op.drop_index("ix_moderation_review_entity_id", table_name="moderation_review")
    op.drop_index("ix_moderation_review_entity_type", table_name="moderation_review")
    op.drop_index("ix_moderation_review_id", table_name="moderation_review")
    op.drop_table("moderation_review")
