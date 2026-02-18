"""Add deal_sync table for frontend deals sync

Revision ID: 20260212_deal
Revises: e842bea3c69d
Create Date: 2026-02-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260212_deal"
down_revision: Union[str, None] = "e842bea3c69d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not insp.has_table("deal_sync"):
        op.create_table(
            "deal_sync",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("local_id", sa.String(length=120), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # индексы тоже делаем безопасно (на случай если таблица есть, а индексов нет)
    existing = (
        {ix["name"] for ix in insp.get_indexes("deal_sync")}
        if insp.has_table("deal_sync")
        else set()
    )

    if op.f("ix_deal_sync_id") not in existing:
        op.create_index(op.f("ix_deal_sync_id"), "deal_sync", ["id"], unique=False)

    if op.f("ix_deal_sync_local_id") not in existing:
        op.create_index(
            op.f("ix_deal_sync_local_id"), "deal_sync", ["local_id"], unique=True
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_deal_sync_local_id"), table_name="deal_sync")
    op.drop_index(op.f("ix_deal_sync_id"), table_name="deal_sync")
    op.drop_table("deal_sync")
