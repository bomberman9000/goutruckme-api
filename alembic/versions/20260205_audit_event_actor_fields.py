"""Add actor fields to audit_events.

Revision ID: 20260205_ae_actor
Revises: 20260204_ae
Create Date: 2026-02-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260205_ae_actor"
down_revision: Union[str, None] = "20260204_ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("actor_role", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_audit_events_actor_role",
        "audit_events",
        ["actor_role"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_actor_user_id",
        "audit_events",
        ["actor_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_role", table_name="audit_events")
    op.drop_column("audit_events", "actor_user_id")
    op.drop_column("audit_events", "actor_role")
