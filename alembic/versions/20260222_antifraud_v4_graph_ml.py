"""add antifraud v4 graph, signals, enforcement and model tables

Revision ID: 20260222_antifraud_v4_graph_ml
Revises: 20260222_antifraud_v3_learning
Create Date: 2026-02-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260222_antifraud_v4_graph_ml"
down_revision: Union[str, None] = "20260222_antifraud_v3_learning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "fraud_entities" not in tables:
        op.create_table(
            "fraud_entities",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("entity_type", sa.String(length=32), nullable=False),
            sa.Column("entity_value", sa.String(length=512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("entity_type", "entity_value", name="uq_fraud_entity_type_value"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_entities_entity_type ON fraud_entities (entity_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_entities_entity_value ON fraud_entities (entity_value)")

    if "fraud_edges" not in tables:
        op.create_table(
            "fraud_edges",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("src_entity_id", sa.Integer(), sa.ForeignKey("fraud_entities.id"), nullable=False),
            sa.Column("dst_entity_id", sa.Integer(), sa.ForeignKey("fraud_entities.id"), nullable=False),
            sa.Column("edge_type", sa.String(length=32), nullable=False),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("evidence", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_edges_src_entity_id ON fraud_edges (src_entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_edges_dst_entity_id ON fraud_edges (dst_entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_edges_edge_type ON fraud_edges (edge_type)")

    if "fraud_components" not in tables:
        op.create_table(
            "fraud_components",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("component_key", sa.String(length=128), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("high_risk_nodes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("component_key", name="uq_fraud_component_key"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_components_component_key ON fraud_components (component_key)")

    if "fraud_entity_components" not in tables:
        op.create_table(
            "fraud_entity_components",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("entity_id", sa.Integer(), sa.ForeignKey("fraud_entities.id"), nullable=False),
            sa.Column("component_id", sa.Integer(), sa.ForeignKey("fraud_components.id"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("entity_id", name="uq_fraud_entity_component_entity"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_entity_components_entity_id ON fraud_entity_components (entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_entity_components_component_id ON fraud_entity_components (component_id)")

    if "fraud_signals" not in tables:
        op.create_table(
            "fraud_signals",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("signal_type", sa.String(length=40), nullable=False),
            sa.Column("entity_id", sa.Integer(), sa.ForeignKey("fraud_entities.id"), nullable=True),
            sa.Column("deal_id", sa.Integer(), nullable=True),
            sa.Column("severity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_signals_signal_type ON fraud_signals (signal_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_signals_entity_id ON fraud_signals (entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fraud_signals_deal_id ON fraud_signals (deal_id)")

    if "enforcement_decisions" not in tables:
        op.create_table(
            "enforcement_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("scope", sa.String(length=32), nullable=False),
            sa.Column("scope_id", sa.String(length=128), nullable=False),
            sa.Column("decision", sa.String(length=24), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_enforcement_decisions_scope ON enforcement_decisions (scope)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_enforcement_decisions_scope_id ON enforcement_decisions (scope_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_enforcement_decisions_decision ON enforcement_decisions (decision)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_enforcement_decisions_scope_scope_id ON enforcement_decisions (scope, scope_id)")

    if "antifraud_models" not in tables:
        op.create_table(
            "antifraud_models",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("model_type", sa.String(length=24), nullable=False, server_default="logreg"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("weights", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=True),
            sa.Column("trained_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_antifraud_models_version ON antifraud_models (version)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_antifraud_models_is_active ON antifraud_models (is_active)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    op.execute("DROP INDEX IF EXISTS ix_antifraud_models_is_active")
    op.execute("DROP INDEX IF EXISTS ix_antifraud_models_version")
    if "antifraud_models" in tables:
        op.drop_table("antifraud_models")

    op.execute("DROP INDEX IF EXISTS ix_enforcement_decisions_scope_scope_id")
    op.execute("DROP INDEX IF EXISTS ix_enforcement_decisions_decision")
    op.execute("DROP INDEX IF EXISTS ix_enforcement_decisions_scope_id")
    op.execute("DROP INDEX IF EXISTS ix_enforcement_decisions_scope")
    if "enforcement_decisions" in tables:
        op.drop_table("enforcement_decisions")

    op.execute("DROP INDEX IF EXISTS ix_fraud_signals_deal_id")
    op.execute("DROP INDEX IF EXISTS ix_fraud_signals_entity_id")
    op.execute("DROP INDEX IF EXISTS ix_fraud_signals_signal_type")
    if "fraud_signals" in tables:
        op.drop_table("fraud_signals")

    op.execute("DROP INDEX IF EXISTS ix_fraud_entity_components_component_id")
    op.execute("DROP INDEX IF EXISTS ix_fraud_entity_components_entity_id")
    if "fraud_entity_components" in tables:
        op.drop_table("fraud_entity_components")

    op.execute("DROP INDEX IF EXISTS ix_fraud_components_component_key")
    if "fraud_components" in tables:
        op.drop_table("fraud_components")

    op.execute("DROP INDEX IF EXISTS ix_fraud_edges_edge_type")
    op.execute("DROP INDEX IF EXISTS ix_fraud_edges_dst_entity_id")
    op.execute("DROP INDEX IF EXISTS ix_fraud_edges_src_entity_id")
    if "fraud_edges" in tables:
        op.drop_table("fraud_edges")

    op.execute("DROP INDEX IF EXISTS ix_fraud_entities_entity_value")
    op.execute("DROP INDEX IF EXISTS ix_fraud_entities_entity_type")
    if "fraud_entities" in tables:
        op.drop_table("fraud_entities")
