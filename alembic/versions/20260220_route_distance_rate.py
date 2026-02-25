"""add route distance and rate fields to loads

Revision ID: 20260220_route_distance_rate
Revises: 20260220_cities_autocomplete
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_route_distance_rate"
down_revision: Union[str, None] = "20260220_cities_autocomplete"
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
        if "total_price" not in load_columns:
            batch_op.add_column(sa.Column("total_price", sa.Float(), nullable=True))
        if "distance_km" not in load_columns:
            batch_op.add_column(sa.Column("distance_km", sa.Float(), nullable=True))
        if "rate_per_km" not in load_columns:
            batch_op.add_column(sa.Column("rate_per_km", sa.Float(), nullable=True))

    op.execute("UPDATE loads SET total_price = price WHERE total_price IS NULL AND price IS NOT NULL")
    op.execute("UPDATE loads SET price = total_price WHERE price IS NULL AND total_price IS NOT NULL")
    op.execute(
        "UPDATE loads "
        "SET rate_per_km = ROUND(CAST((COALESCE(total_price, price) / NULLIF(distance_km, 0)) AS numeric), 1) "
        "WHERE rate_per_km IS NULL AND distance_km IS NOT NULL AND distance_km > 0 "
        "AND COALESCE(total_price, price) IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "loads" not in tables:
        return

    load_columns = _column_names(inspector, "loads")
    with op.batch_alter_table("loads") as batch_op:
        if "rate_per_km" in load_columns:
            batch_op.drop_column("rate_per_km")
        if "distance_km" in load_columns:
            batch_op.drop_column("distance_km")
        if "total_price" in load_columns:
            batch_op.drop_column("total_price")
