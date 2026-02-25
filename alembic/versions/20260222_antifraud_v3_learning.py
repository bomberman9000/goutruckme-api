"""add antifraud v3 learning and analytics tables

Revision ID: 20260222_antifraud_v3_learning
Revises: 20260222_antifraud_v2
Create Date: 2026-02-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260222_antifraud_v3_learning"
down_revision: Union[str, None] = "20260222_antifraud_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "closed_deal_stats" not in tables:
        op.create_table(
            "closed_deal_stats",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("from_city_norm", sa.String(length=255), nullable=False),
            sa.Column("to_city_norm", sa.String(length=255), nullable=False),
            sa.Column("distance_km", sa.Float(), nullable=False),
            sa.Column("rate_per_km", sa.Float(), nullable=False),
            sa.Column("total_rub", sa.Float(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_closed_deal_stats_from_city_norm ON closed_deal_stats (from_city_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_closed_deal_stats_to_city_norm ON closed_deal_stats (to_city_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_closed_deal_stats_closed_at ON closed_deal_stats (closed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_closed_deal_stats_route_pair ON closed_deal_stats (from_city_norm, to_city_norm)")

    if "counterparty_risk_history" not in tables:
        op.create_table(
            "counterparty_risk_history",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("counterparty_inn", sa.String(length=20), nullable=False),
            sa.Column("deal_id", sa.Integer(), nullable=False),
            sa.Column("risk_level", sa.String(length=20), nullable=False),
            sa.Column("score_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_risk_history_counterparty_inn ON counterparty_risk_history (counterparty_inn)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_risk_history_deal_id ON counterparty_risk_history (deal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_risk_history_risk_level ON counterparty_risk_history (risk_level)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_risk_history_created_at ON counterparty_risk_history (created_at)")

    if "route_rate_stats" not in tables:
        op.create_table(
            "route_rate_stats",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("from_city_norm", sa.String(length=255), nullable=False),
            sa.Column("to_city_norm", sa.String(length=255), nullable=False),
            sa.Column("mean_rate", sa.Float(), nullable=True),
            sa.Column("median_rate", sa.Float(), nullable=True),
            sa.Column("std_dev", sa.Float(), nullable=True),
            sa.Column("p25", sa.Float(), nullable=True),
            sa.Column("p75", sa.Float(), nullable=True),
            sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_stats_pair"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_route_rate_stats_from_city_norm ON route_rate_stats (from_city_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_route_rate_stats_to_city_norm ON route_rate_stats (to_city_norm)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    op.execute("DROP INDEX IF EXISTS ix_route_rate_stats_to_city_norm")
    op.execute("DROP INDEX IF EXISTS ix_route_rate_stats_from_city_norm")
    if "route_rate_stats" in tables:
        op.drop_table("route_rate_stats")

    op.execute("DROP INDEX IF EXISTS ix_counterparty_risk_history_created_at")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_risk_history_risk_level")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_risk_history_deal_id")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_risk_history_counterparty_inn")
    if "counterparty_risk_history" in tables:
        op.drop_table("counterparty_risk_history")

    op.execute("DROP INDEX IF EXISTS ix_closed_deal_stats_route_pair")
    op.execute("DROP INDEX IF EXISTS ix_closed_deal_stats_closed_at")
    op.execute("DROP INDEX IF EXISTS ix_closed_deal_stats_to_city_norm")
    op.execute("DROP INDEX IF EXISTS ix_closed_deal_stats_from_city_norm")
    if "closed_deal_stats" in tables:
        op.drop_table("closed_deal_stats")
