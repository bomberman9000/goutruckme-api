"""add company trust stats aggregate table

Revision ID: 20260218_trust
Revises: 20260218_userrole
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260218_trust"
down_revision: Union[str, None] = "20260218_userrole"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_trust_stats",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("trust_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("stars", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("deals_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deals_success", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disputes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disputes_confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flags_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flags_high", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_completeness", sa.Float(), nullable=False, server_default="0"),
        sa.Column("response_time_avg_min", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_index("ix_company_trust_stats_company_id", "company_trust_stats", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_company_trust_stats_company_id", table_name="company_trust_stats")
    op.drop_table("company_trust_stats")
