"""shipments registry with payments and attachments

Revision ID: 20260219_shipments
Revises: 20260219_doc_sign
Create Date: 2026-02-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260219_shipments"
down_revision: Union[str, None] = "20260219_doc_sign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner_company_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ship_date", sa.Date(), nullable=False),
        sa.Column("client_name", sa.String(length=255), nullable=False),
        sa.Column("client_inn", sa.String(length=20), nullable=True),
        sa.Column("from_city", sa.String(length=120), nullable=False),
        sa.Column("to_city", sa.String(length=120), nullable=False),
        sa.Column("cargo_brief", sa.Text(), nullable=False),
        sa.Column("carrier_name", sa.String(length=255), nullable=False),
        sa.Column("carrier_inn", sa.String(length=20), nullable=True),
        sa.Column("client_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carrier_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_shipments_owner_company_id", "shipments", ["owner_company_id"], unique=False)
    op.create_index("ix_shipments_ship_date", "shipments", ["ship_date"], unique=False)
    op.create_index("ix_shipments_status", "shipments", ["status"], unique=False)
    op.create_index("ix_shipments_client_inn", "shipments", ["client_inn"], unique=False)
    op.create_index("ix_shipments_carrier_inn", "shipments", ["carrier_inn"], unique=False)
    op.create_index("ix_shipments_from_city", "shipments", ["from_city"], unique=False)
    op.create_index("ix_shipments_to_city", "shipments", ["to_city"], unique=False)

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("planned_amount", sa.Float(), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("actual_amount", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payments_shipment_id", "payments", ["shipment_id"], unique=False)
    op.create_index("ix_payments_direction", "payments", ["direction"], unique=False)
    op.create_index("ix_payments_planned_date", "payments", ["planned_date"], unique=False)
    op.create_index("ix_payments_actual_date", "payments", ["actual_date"], unique=False)
    op.create_index("ix_payments_status", "payments", ["status"], unique=False)

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_attachments_shipment_id", "attachments", ["shipment_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_attachments_shipment_id", table_name="attachments")
    op.drop_table("attachments")

    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_actual_date", table_name="payments")
    op.drop_index("ix_payments_planned_date", table_name="payments")
    op.drop_index("ix_payments_direction", table_name="payments")
    op.drop_index("ix_payments_shipment_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_shipments_to_city", table_name="shipments")
    op.drop_index("ix_shipments_from_city", table_name="shipments")
    op.drop_index("ix_shipments_carrier_inn", table_name="shipments")
    op.drop_index("ix_shipments_client_inn", table_name="shipments")
    op.drop_index("ix_shipments_status", table_name="shipments")
    op.drop_index("ix_shipments_ship_date", table_name="shipments")
    op.drop_index("ix_shipments_owner_company_id", table_name="shipments")
    op.drop_table("shipments")
