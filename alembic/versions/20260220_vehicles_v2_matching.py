"""vehicles v2 matching fields and indexes

Revision ID: 20260220_vehicles_v2_matching
Revises: 20260220_vehicle_registry
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_vehicles_v2_matching"
down_revision: Union[str, None] = "20260220_vehicle_registry"
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

    if "vehicles" in tables:
        vehicle_columns = _column_names(inspector, "vehicles")
        with op.batch_alter_table("vehicles") as batch_op:
            if "adr_classes" not in vehicle_columns:
                batch_op.add_column(sa.Column("adr_classes", sa.JSON(), nullable=True))
            if "crew_size" not in vehicle_columns:
                batch_op.add_column(sa.Column("crew_size", sa.Integer(), nullable=True, server_default="1"))

        op.execute("UPDATE vehicles SET crew_size = COALESCE(crew_size, 1)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_owner_status ON vehicles (owner_user_id, status)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_crew_size ON vehicles (crew_size)")

        if bind.dialect.name == "postgresql":
            op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_options_gin ON vehicles USING GIN ((options::jsonb))")
            op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_adr_classes_gin ON vehicles USING GIN ((adr_classes::jsonb))")

    if "loads" in tables:
        load_columns = _column_names(inspector, "loads")
        with op.batch_alter_table("loads") as batch_op:
            if "required_vehicle_kinds" not in load_columns:
                batch_op.add_column(sa.Column("required_vehicle_kinds", sa.JSON(), nullable=True))
            if "required_options" not in load_columns:
                batch_op.add_column(sa.Column("required_options", sa.JSON(), nullable=True))
            if "adr_classes" not in load_columns:
                batch_op.add_column(sa.Column("adr_classes", sa.JSON(), nullable=True))
            if "crew_required" not in load_columns:
                batch_op.add_column(sa.Column("crew_required", sa.Boolean(), nullable=True, server_default=sa.false()))
            if "temp_min" not in load_columns:
                batch_op.add_column(sa.Column("temp_min", sa.Float(), nullable=True))
            if "temp_max" not in load_columns:
                batch_op.add_column(sa.Column("temp_max", sa.Float(), nullable=True))

        op.execute("UPDATE loads SET crew_required = COALESCE(crew_required, FALSE)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_loads_status_from_to ON loads (status, from_city_id, to_city_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_loads_crew_required ON loads (crew_required)")

        if bind.dialect.name == "postgresql":
            op.execute("CREATE INDEX IF NOT EXISTS ix_loads_required_options_gin ON loads USING GIN ((required_options::jsonb))")
            op.execute("CREATE INDEX IF NOT EXISTS ix_loads_required_vehicle_kinds_gin ON loads USING GIN ((required_vehicle_kinds::jsonb))")
            op.execute("CREATE INDEX IF NOT EXISTS ix_loads_adr_classes_gin ON loads USING GIN ((adr_classes::jsonb))")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    op.execute("DROP INDEX IF EXISTS ix_loads_adr_classes_gin")
    op.execute("DROP INDEX IF EXISTS ix_loads_required_vehicle_kinds_gin")
    op.execute("DROP INDEX IF EXISTS ix_loads_required_options_gin")
    op.execute("DROP INDEX IF EXISTS ix_loads_crew_required")
    op.execute("DROP INDEX IF EXISTS ix_loads_status_from_to")

    if "loads" in tables:
        load_columns = _column_names(inspector, "loads")
        with op.batch_alter_table("loads") as batch_op:
            if "temp_max" in load_columns:
                batch_op.drop_column("temp_max")
            if "temp_min" in load_columns:
                batch_op.drop_column("temp_min")
            if "crew_required" in load_columns:
                batch_op.drop_column("crew_required")
            if "adr_classes" in load_columns:
                batch_op.drop_column("adr_classes")
            if "required_options" in load_columns:
                batch_op.drop_column("required_options")
            if "required_vehicle_kinds" in load_columns:
                batch_op.drop_column("required_vehicle_kinds")

    op.execute("DROP INDEX IF EXISTS ix_vehicles_adr_classes_gin")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_options_gin")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_crew_size")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_owner_status")

    if "vehicles" in tables:
        vehicle_columns = _column_names(inspector, "vehicles")
        with op.batch_alter_table("vehicles") as batch_op:
            if "crew_size" in vehicle_columns:
                batch_op.drop_column("crew_size")
            if "adr_classes" in vehicle_columns:
                batch_op.drop_column("adr_classes")
