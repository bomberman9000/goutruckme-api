"""add sync enrichment fields to loads

Revision ID: 20260301_load_sync_enrichment_fields
Revises: 20260222_antifraud_v4_graph_ml
Create Date: 2026-03-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260301_load_sync_enrichment_fields"
down_revision: Union[str, None] = "20260222_antifraud_v4_graph_ml"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "loads" not in tables:
        return

    columns_to_add: list[tuple[str, sa.Column]] = [
        ("cargo_description", sa.Column("cargo_description", sa.String(length=255), nullable=True)),
        ("payment_terms", sa.Column("payment_terms", sa.String(length=120), nullable=True)),
        ("is_direct_customer", sa.Column("is_direct_customer", sa.Boolean(), nullable=True)),
        ("dimensions", sa.Column("dimensions", sa.String(length=64), nullable=True)),
        ("is_hot_deal", sa.Column("is_hot_deal", sa.Boolean(), nullable=True, server_default=sa.text("false"))),
        ("phone", sa.Column("phone", sa.String(length=32), nullable=True)),
        ("inn", sa.Column("inn", sa.String(length=12), nullable=True)),
        ("suggested_response", sa.Column("suggested_response", sa.Text(), nullable=True)),
        ("source", sa.Column("source", sa.String(length=64), nullable=True)),
    ]

    for column_name, column in columns_to_add:
        if not _has_column(inspector, "loads", column_name):
            op.add_column("loads", column)

    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_phone ON loads (phone)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_inn ON loads (inn)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "loads" not in tables:
        return

    op.execute("DROP INDEX IF EXISTS ix_loads_phone")
    op.execute("DROP INDEX IF EXISTS ix_loads_inn")

    for column_name in [
        "source",
        "suggested_response",
        "inn",
        "phone",
        "is_hot_deal",
        "dimensions",
        "is_direct_customer",
        "payment_terms",
        "cargo_description",
    ]:
        inspector = sa.inspect(bind)
        if _has_column(inspector, "loads", column_name):
            op.drop_column("loads", column_name)
