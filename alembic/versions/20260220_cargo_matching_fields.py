"""add cargo matching fields to loads

Revision ID: 20260220_cargo_matching_fields
Revises: 20260220_vehicle_values_normalize
Create Date: 2026-02-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_cargo_matching_fields"
down_revision: Union[str, None] = "20260220_vehicle_values_normalize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "loads" not in tables:
        return

    load_columns = _column_names(inspector, "loads")
    with op.batch_alter_table("loads") as batch_op:
        if "cargo_kind" not in load_columns:
            batch_op.add_column(sa.Column("cargo_kind", sa.String(length=32), nullable=True))
        if "container_size" not in load_columns:
            batch_op.add_column(sa.Column("container_size", sa.String(length=8), nullable=True))
        if "needs_crane" not in load_columns:
            batch_op.add_column(sa.Column("needs_crane", sa.Boolean(), nullable=True, server_default=sa.false()))
        if "needs_dump" not in load_columns:
            batch_op.add_column(sa.Column("needs_dump", sa.Boolean(), nullable=True, server_default=sa.false()))

    op.execute("UPDATE loads SET needs_crane = COALESCE(needs_crane, FALSE)")
    op.execute("UPDATE loads SET needs_dump = COALESCE(needs_dump, FALSE)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_cargo_kind ON loads (cargo_kind)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    op.execute("DROP INDEX IF EXISTS ix_loads_cargo_kind")

    if "loads" not in tables:
        return

    load_columns = _column_names(inspector, "loads")
    with op.batch_alter_table("loads") as batch_op:
        if "needs_dump" in load_columns:
            batch_op.drop_column("needs_dump")
        if "needs_crane" in load_columns:
            batch_op.drop_column("needs_crane")
        if "container_size" in load_columns:
            batch_op.drop_column("container_size")
        if "cargo_kind" in load_columns:
            batch_op.drop_column("cargo_kind")
