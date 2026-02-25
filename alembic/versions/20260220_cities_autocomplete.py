"""cities catalog + load city ids for autocomplete

Revision ID: 20260220_cities_autocomplete
Revises: 20260219_consolidation
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_cities_autocomplete"
down_revision: Union[str, None] = "20260219_consolidation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _fk_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    names: set[str] = set()
    for fk in inspector.get_foreign_keys(table_name):
        name = fk.get("name")
        if name:
            names.add(name)
    return names


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "cities" not in tables:
        op.create_table(
            "cities",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("name_norm", sa.String(length=120), nullable=False),
            sa.Column("region", sa.String(length=120), nullable=True),
            sa.Column("country", sa.String(length=8), nullable=False, server_default="RU"),
            sa.Column("population", sa.Integer(), nullable=True),
            sa.Column("lat", sa.Float(), nullable=True),
            sa.Column("lon", sa.Float(), nullable=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cities_name_norm ON cities (name_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cities_country ON cities (country)")

    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_cities_name_norm_trgm "
            "ON cities USING gin (name_norm gin_trgm_ops)"
        )

    if "loads" in tables:
        load_columns = _column_names(inspector, "loads")
        if "from_city_id" not in load_columns:
            op.add_column("loads", sa.Column("from_city_id", sa.Integer(), nullable=True))
        if "to_city_id" not in load_columns:
            op.add_column("loads", sa.Column("to_city_id", sa.Integer(), nullable=True))
        if "from_city_text" not in load_columns:
            op.add_column("loads", sa.Column("from_city_text", sa.String(length=255), nullable=True))
        if "to_city_text" not in load_columns:
            op.add_column("loads", sa.Column("to_city_text", sa.String(length=255), nullable=True))

        op.execute("UPDATE loads SET from_city_text = from_city WHERE from_city_text IS NULL")
        op.execute("UPDATE loads SET to_city_text = to_city WHERE to_city_text IS NULL")

        op.execute("CREATE INDEX IF NOT EXISTS ix_loads_from_city_id ON loads (from_city_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_loads_to_city_id ON loads (to_city_id)")

        if bind.dialect.name != "sqlite":
            fk_names = _fk_names(inspector, "loads")
            if "fk_loads_from_city_id" not in fk_names:
                op.create_foreign_key(
                    "fk_loads_from_city_id",
                    "loads",
                    "cities",
                    ["from_city_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "fk_loads_to_city_id" not in fk_names:
                op.create_foreign_key(
                    "fk_loads_to_city_id",
                    "loads",
                    "cities",
                    ["to_city_id"],
                    ["id"],
                    ondelete="SET NULL",
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "loads" in tables:
        if bind.dialect.name != "sqlite":
            fk_names = _fk_names(inspector, "loads")
            if "fk_loads_to_city_id" in fk_names:
                op.drop_constraint("fk_loads_to_city_id", "loads", type_="foreignkey")
            if "fk_loads_from_city_id" in fk_names:
                op.drop_constraint("fk_loads_from_city_id", "loads", type_="foreignkey")

        load_columns = _column_names(inspector, "loads")
        with op.batch_alter_table("loads") as batch_op:
            if "to_city_text" in load_columns:
                batch_op.drop_column("to_city_text")
            if "from_city_text" in load_columns:
                batch_op.drop_column("from_city_text")
            if "to_city_id" in load_columns:
                batch_op.drop_column("to_city_id")
            if "from_city_id" in load_columns:
                batch_op.drop_column("from_city_id")

        op.execute("DROP INDEX IF EXISTS ix_loads_to_city_id")
        op.execute("DROP INDEX IF EXISTS ix_loads_from_city_id")

    if "cities" in tables:
        op.execute("DROP INDEX IF EXISTS ix_cities_name_norm_trgm")
        op.execute("DROP INDEX IF EXISTS ix_cities_country")
        op.execute("DROP INDEX IF EXISTS ix_cities_name_norm")
        op.drop_table("cities")
