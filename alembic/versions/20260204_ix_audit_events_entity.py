"""Create audit_events table and index (entity_type, entity_id, created_at)

Revision ID: 20260204_ae
Revises: e842bea3c69d
Create Date: 2026-02-04

Таблица audit_events + индекс для выборки истории по entity.
actor_role/actor_user_id добавляются в 20260205_ae_actor.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260204_ae"
down_revision: Union[str, None] = "e842bea3c69d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_type", "entity_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_events_entity_type"), "audit_events", ["entity_type"])
    op.create_index(op.f("ix_audit_events_entity_id"), "audit_events", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_entity_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_entity_type"), table_name="audit_events")
    op.drop_table("audit_events")
