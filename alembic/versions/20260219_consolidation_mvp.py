"""consolidation plans + compatibility fields

Revision ID: 20260219_consolidation
Revises: 20260219_cargo_status
Create Date: 2026-02-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260219_consolidation"
down_revision: Union[str, None] = "20260219_cargo_status"
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
        vehicle_cols = _column_names(inspector, "vehicles")
        if "max_weight_t" not in vehicle_cols:
            op.add_column("vehicles", sa.Column("max_weight_t", sa.Float(), nullable=True))
        if "max_volume_m3" not in vehicle_cols:
            op.add_column("vehicles", sa.Column("max_volume_m3", sa.Float(), nullable=True))
        if "start_lat" not in vehicle_cols:
            op.add_column("vehicles", sa.Column("start_lat", sa.Float(), nullable=True))
        if "start_lon" not in vehicle_cols:
            op.add_column("vehicles", sa.Column("start_lon", sa.Float(), nullable=True))

        op.execute(
            "UPDATE vehicles "
            "SET max_weight_t = COALESCE(max_weight_t, capacity_tons), "
            "max_volume_m3 = COALESCE(max_volume_m3, volume_m3)"
        )

    if "loads" in tables:
        load_cols = _column_names(inspector, "loads")
        if "weight_t" not in load_cols:
            op.add_column("loads", sa.Column("weight_t", sa.Float(), nullable=True))
        if "volume_m3" not in load_cols:
            op.add_column("loads", sa.Column("volume_m3", sa.Float(), nullable=True))
        if "pickup_lat" not in load_cols:
            op.add_column("loads", sa.Column("pickup_lat", sa.Float(), nullable=True))
        if "pickup_lon" not in load_cols:
            op.add_column("loads", sa.Column("pickup_lon", sa.Float(), nullable=True))
        if "delivery_lat" not in load_cols:
            op.add_column("loads", sa.Column("delivery_lat", sa.Float(), nullable=True))
        if "delivery_lon" not in load_cols:
            op.add_column("loads", sa.Column("delivery_lon", sa.Float(), nullable=True))
        if "required_body_type" not in load_cols:
            op.add_column("loads", sa.Column("required_body_type", sa.String(length=32), nullable=True))
        if "adr_class" not in load_cols:
            op.add_column("loads", sa.Column("adr_class", sa.String(length=32), nullable=True))
        if "temp_required" not in load_cols:
            op.add_column("loads", sa.Column("temp_required", sa.Boolean(), nullable=True, server_default=sa.false()))
        if "loading_type" not in load_cols:
            op.add_column("loads", sa.Column("loading_type", sa.String(length=32), nullable=True))

        op.execute(
            "UPDATE loads "
            "SET weight_t = COALESCE(weight_t, weight), "
            "volume_m3 = COALESCE(volume_m3, volume)"
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_loads_required_body_type ON loads (required_body_type)")

    if "consolidation_plans" not in tables:
        op.create_table(
            "consolidation_plans",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("total_weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_volume", sa.Float(), nullable=False, server_default="0"),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("detour_km", sa.Float(), nullable=False, server_default="0"),
            sa.Column("explain_json", sa.JSON(), nullable=True),
        )
        op.create_index("ix_consolidation_plans_vehicle_id", "consolidation_plans", ["vehicle_id"], unique=False)
        op.create_index("ix_consolidation_plans_status", "consolidation_plans", ["status"], unique=False)
        op.create_index("ix_consolidation_plans_created_by", "consolidation_plans", ["created_by"], unique=False)
        op.create_index("ix_consolidation_plans_created_at", "consolidation_plans", ["created_at"], unique=False)

    if "consolidation_plan_items" not in tables:
        op.create_table(
            "consolidation_plan_items",
            sa.Column("plan_id", sa.Integer(), sa.ForeignKey("consolidation_plans.id", ondelete="CASCADE"), primary_key=True, nullable=False),
            sa.Column("cargo_id", sa.Integer(), sa.ForeignKey("loads.id", ondelete="CASCADE"), primary_key=True, nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="1"),
            sa.UniqueConstraint("plan_id", "seq", name="uq_consolidation_plan_items_plan_seq"),
        )
        op.create_index("ix_consolidation_plan_items_cargo_id", "consolidation_plan_items", ["cargo_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "consolidation_plan_items" in tables:
        op.drop_index("ix_consolidation_plan_items_cargo_id", table_name="consolidation_plan_items")
        op.drop_table("consolidation_plan_items")
    if "consolidation_plans" in tables:
        op.drop_index("ix_consolidation_plans_created_at", table_name="consolidation_plans")
        op.drop_index("ix_consolidation_plans_created_by", table_name="consolidation_plans")
        op.drop_index("ix_consolidation_plans_status", table_name="consolidation_plans")
        op.drop_index("ix_consolidation_plans_vehicle_id", table_name="consolidation_plans")
        op.drop_table("consolidation_plans")

    if "loads" in tables:
        load_cols = _column_names(inspector, "loads")
        op.execute("DROP INDEX IF EXISTS ix_loads_required_body_type")
        with op.batch_alter_table("loads") as batch_op:
            if "loading_type" in load_cols:
                batch_op.drop_column("loading_type")
            if "temp_required" in load_cols:
                batch_op.drop_column("temp_required")
            if "adr_class" in load_cols:
                batch_op.drop_column("adr_class")
            if "required_body_type" in load_cols:
                batch_op.drop_column("required_body_type")
            if "delivery_lon" in load_cols:
                batch_op.drop_column("delivery_lon")
            if "delivery_lat" in load_cols:
                batch_op.drop_column("delivery_lat")
            if "pickup_lon" in load_cols:
                batch_op.drop_column("pickup_lon")
            if "pickup_lat" in load_cols:
                batch_op.drop_column("pickup_lat")
            if "volume_m3" in load_cols:
                batch_op.drop_column("volume_m3")
            if "weight_t" in load_cols:
                batch_op.drop_column("weight_t")

    if "vehicles" in tables:
        vehicle_cols = _column_names(inspector, "vehicles")
        with op.batch_alter_table("vehicles") as batch_op:
            if "start_lon" in vehicle_cols:
                batch_op.drop_column("start_lon")
            if "start_lat" in vehicle_cols:
                batch_op.drop_column("start_lat")
            if "max_volume_m3" in vehicle_cols:
                batch_op.drop_column("max_volume_m3")
            if "max_weight_t" in vehicle_cols:
                batch_op.drop_column("max_weight_t")
