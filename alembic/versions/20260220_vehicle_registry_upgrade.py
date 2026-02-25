"""expand vehicles registry fields for ATI-like cabinet

Revision ID: 20260220_vehicle_registry
Revises: 20260220_loading_time_locale_fix
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_vehicle_registry"
down_revision: Union[str, None] = "20260220_loading_time_locale_fix"
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
    if "vehicles" not in tables:
        return

    vehicle_columns = _column_names(inspector, "vehicles")
    with op.batch_alter_table("vehicles") as batch_op:
        if "owner_user_id" not in vehicle_columns:
            batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        if "name" not in vehicle_columns:
            batch_op.add_column(sa.Column("name", sa.String(length=120), nullable=True))
        if "vehicle_kind" not in vehicle_columns:
            batch_op.add_column(sa.Column("vehicle_kind", sa.String(length=40), nullable=True))
        if "brand" not in vehicle_columns:
            batch_op.add_column(sa.Column("brand", sa.String(length=64), nullable=True))
        if "model" not in vehicle_columns:
            batch_op.add_column(sa.Column("model", sa.String(length=64), nullable=True))
        if "plate_number" not in vehicle_columns:
            batch_op.add_column(sa.Column("plate_number", sa.String(length=24), nullable=True))
        if "vin" not in vehicle_columns:
            batch_op.add_column(sa.Column("vin", sa.String(length=64), nullable=True))
        if "pts_number" not in vehicle_columns:
            batch_op.add_column(sa.Column("pts_number", sa.String(length=64), nullable=True))
        if "payload_tons" not in vehicle_columns:
            batch_op.add_column(sa.Column("payload_tons", sa.Float(), nullable=True))
        if "length_m" not in vehicle_columns:
            batch_op.add_column(sa.Column("length_m", sa.Float(), nullable=True))
        if "width_m" not in vehicle_columns:
            batch_op.add_column(sa.Column("width_m", sa.Float(), nullable=True))
        if "height_m" not in vehicle_columns:
            batch_op.add_column(sa.Column("height_m", sa.Float(), nullable=True))
        if "loading_types" not in vehicle_columns:
            batch_op.add_column(sa.Column("loading_types", sa.JSON(), nullable=True))
        if "options" not in vehicle_columns:
            batch_op.add_column(sa.Column("options", sa.JSON(), nullable=True))
        if "temp_min" not in vehicle_columns:
            batch_op.add_column(sa.Column("temp_min", sa.Float(), nullable=True))
        if "temp_max" not in vehicle_columns:
            batch_op.add_column(sa.Column("temp_max", sa.Float(), nullable=True))
        if "city_id" not in vehicle_columns:
            batch_op.add_column(sa.Column("city_id", sa.Integer(), nullable=True))
        if "radius_km" not in vehicle_columns:
            batch_op.add_column(sa.Column("radius_km", sa.Integer(), nullable=True, server_default="50"))
        if "available_to" not in vehicle_columns:
            batch_op.add_column(sa.Column("available_to", sa.Date(), nullable=True))
        if "updated_at" not in vehicle_columns:
            batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    op.execute(
        "UPDATE vehicles SET "
        "owner_user_id = COALESCE(owner_user_id, carrier_id), "
        "payload_tons = COALESCE(payload_tons, max_weight_t, capacity_tons), "
        "radius_km = COALESCE(radius_km, 50), "
        "updated_at = COALESCE(updated_at, created_at)"
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_owner_user_id ON vehicles (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_vehicle_kind ON vehicles (vehicle_kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_plate_number ON vehicles (plate_number)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_city_id ON vehicles (city_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_available_to ON vehicles (available_to)")

    try:
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicle_owner_plate_idx "
            "ON vehicles (owner_user_id, plate_number)"
        )
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "vehicles" not in tables:
        return

    vehicle_columns = _column_names(inspector, "vehicles")
    op.execute("DROP INDEX IF EXISTS uq_vehicle_owner_plate_idx")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_available_to")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_city_id")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_plate_number")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_vehicle_kind")
    op.execute("DROP INDEX IF EXISTS ix_vehicles_owner_user_id")

    with op.batch_alter_table("vehicles") as batch_op:
        if "updated_at" in vehicle_columns:
            batch_op.drop_column("updated_at")
        if "available_to" in vehicle_columns:
            batch_op.drop_column("available_to")
        if "radius_km" in vehicle_columns:
            batch_op.drop_column("radius_km")
        if "city_id" in vehicle_columns:
            batch_op.drop_column("city_id")
        if "temp_max" in vehicle_columns:
            batch_op.drop_column("temp_max")
        if "temp_min" in vehicle_columns:
            batch_op.drop_column("temp_min")
        if "options" in vehicle_columns:
            batch_op.drop_column("options")
        if "loading_types" in vehicle_columns:
            batch_op.drop_column("loading_types")
        if "height_m" in vehicle_columns:
            batch_op.drop_column("height_m")
        if "width_m" in vehicle_columns:
            batch_op.drop_column("width_m")
        if "length_m" in vehicle_columns:
            batch_op.drop_column("length_m")
        if "payload_tons" in vehicle_columns:
            batch_op.drop_column("payload_tons")
        if "pts_number" in vehicle_columns:
            batch_op.drop_column("pts_number")
        if "vin" in vehicle_columns:
            batch_op.drop_column("vin")
        if "plate_number" in vehicle_columns:
            batch_op.drop_column("plate_number")
        if "model" in vehicle_columns:
            batch_op.drop_column("model")
        if "brand" in vehicle_columns:
            batch_op.drop_column("brand")
        if "vehicle_kind" in vehicle_columns:
            batch_op.drop_column("vehicle_kind")
        if "name" in vehicle_columns:
            batch_op.drop_column("name")
        if "owner_user_id" in vehicle_columns:
            batch_op.drop_column("owner_user_id")
