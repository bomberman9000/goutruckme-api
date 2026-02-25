"""add loading_time field for ISO HH:mm storage

Revision ID: 20260220_loading_time_locale_fix
Revises: 20260220_route_distance_rate
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_loading_time_locale_fix"
down_revision: Union[str, None] = "20260220_route_distance_rate"
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
    if "loading_time" not in load_columns:
        with op.batch_alter_table("loads") as batch_op:
            batch_op.add_column(sa.Column("loading_time", sa.String(length=5), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "loads" not in tables:
        return

    load_columns = _column_names(inspector, "loads")
    if "loading_time" in load_columns:
        with op.batch_alter_table("loads") as batch_op:
            batch_op.drop_column("loading_time")
