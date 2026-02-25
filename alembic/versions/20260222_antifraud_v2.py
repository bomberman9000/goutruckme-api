"""add antifraud v2 tables (route rates, counterparty lists, doc requests)

Revision ID: 20260222_antifraud_v2
Revises: 20260220_cargo_matching_fields
Create Date: 2026-02-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260222_antifraud_v2"
down_revision: Union[str, None] = "20260220_cargo_matching_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "route_rate_profiles" not in tables:
        op.create_table(
            "route_rate_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("from_city_norm", sa.String(length=255), nullable=False),
            sa.Column("to_city_norm", sa.String(length=255), nullable=False),
            sa.Column("min_rate_per_km", sa.Integer(), nullable=False),
            sa.Column("max_rate_per_km", sa.Integer(), nullable=False),
            sa.Column("median_rate_per_km", sa.Integer(), nullable=True),
            sa.Column("samples_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("from_city_norm", "to_city_norm", name="uq_route_rate_pair"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_route_rate_profiles_from_city_norm ON route_rate_profiles (from_city_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_route_rate_profiles_to_city_norm ON route_rate_profiles (to_city_norm)")

    if "counterparty_lists" not in tables:
        op.create_table(
            "counterparty_lists",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("list_type", sa.String(length=10), nullable=False),
            sa.Column("inn", sa.String(length=20), nullable=True),
            sa.Column("phone", sa.String(length=32), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_lists_list_type ON counterparty_lists (list_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_lists_inn ON counterparty_lists (inn)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_lists_phone ON counterparty_lists (phone)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_counterparty_lists_name ON counterparty_lists (name)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_counterparty_lists_type_inn_not_null "
        "ON counterparty_lists (list_type, inn) WHERE inn IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_counterparty_lists_type_phone_not_null "
        "ON counterparty_lists (list_type, phone) WHERE phone IS NOT NULL"
    )

    if "deal_doc_requests" not in tables:
        op.create_table(
            "deal_doc_requests",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("deal_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
            sa.Column("required_docs", sa.JSON(), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("deal_id", name="uq_deal_doc_requests_deal_id"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_deal_doc_requests_deal_id ON deal_doc_requests (deal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_deal_doc_requests_status ON deal_doc_requests (status)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    op.execute("DROP INDEX IF EXISTS ix_deal_doc_requests_status")
    op.execute("DROP INDEX IF EXISTS ix_deal_doc_requests_deal_id")
    if "deal_doc_requests" in tables:
        op.drop_table("deal_doc_requests")

    op.execute("DROP INDEX IF EXISTS uq_counterparty_lists_type_phone_not_null")
    op.execute("DROP INDEX IF EXISTS uq_counterparty_lists_type_inn_not_null")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_lists_name")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_lists_phone")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_lists_inn")
    op.execute("DROP INDEX IF EXISTS ix_counterparty_lists_list_type")
    if "counterparty_lists" in tables:
        op.drop_table("counterparty_lists")

    op.execute("DROP INDEX IF EXISTS ix_route_rate_profiles_to_city_norm")
    op.execute("DROP INDEX IF EXISTS ix_route_rate_profiles_from_city_norm")
    if "route_rate_profiles" in tables:
        op.drop_table("route_rate_profiles")
