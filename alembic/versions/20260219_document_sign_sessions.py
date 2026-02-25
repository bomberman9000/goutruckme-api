"""document sign sessions and document payload fields

Revision ID: 20260219_doc_sign
Revises: 20260219_profile
Create Date: 2026-02-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260219_doc_sign"
down_revision: Union[str, None] = "20260219_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name.lower()

    op.add_column("documents", sa.Column("company_id_from", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("company_id_to", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("payload_json", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("pdf_draft_path", sa.String(length=500), nullable=True))
    op.add_column("documents", sa.Column("pdf_signed_path", sa.String(length=500), nullable=True))
    op.add_column("documents", sa.Column("updated_at", sa.DateTime(), nullable=True))

    op.create_index("ix_documents_company_id_from", "documents", ["company_id_from"], unique=False)
    op.create_index("ix_documents_company_id_to", "documents", ["company_id_to"], unique=False)
    if not dialect_name.startswith("sqlite"):
        op.create_foreign_key(
            "fk_documents_company_id_from_users",
            "documents",
            "users",
            ["company_id_from"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "fk_documents_company_id_to_users",
            "documents",
            "users",
            ["company_id_to"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "document_sign_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("otp_hash", sa.String(length=64), nullable=True),
        sa.Column("otp_sent_at", sa.DateTime(), nullable=True),
        sa.Column("otp_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("ip_first", sa.String(length=64), nullable=True),
        sa.Column("user_agent_first", sa.String(length=500), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signature_png_path", sa.String(length=500), nullable=True),
        sa.Column("signature_meta_json", sa.JSON(), nullable=True),
        sa.Column("sms_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_document_sign_sessions_document_id", "document_sign_sessions", ["document_id"], unique=False)
    op.create_index("ix_document_sign_sessions_expires_at", "document_sign_sessions", ["expires_at"], unique=False)
    op.create_index("ix_document_sign_sessions_token_hash", "document_sign_sessions", ["token_hash"], unique=True)

    if dialect_name.startswith("postgres"):
        op.execute(
            sa.text(
                """
                UPDATE documents
                SET
                    company_id_from = deals.shipper_id,
                    company_id_to = deals.carrier_id,
                    updated_at = COALESCE(documents.created_at, CURRENT_TIMESTAMP)
                FROM deals
                WHERE deals.id = documents.deal_id
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE documents
                SET
                    company_id_from = (
                        SELECT shipper_id FROM deals WHERE deals.id = documents.deal_id
                    ),
                    company_id_to = (
                        SELECT carrier_id FROM deals WHERE deals.id = documents.deal_id
                    ),
                    updated_at = COALESCE(documents.created_at, CURRENT_TIMESTAMP)
                """
            )
        )


def downgrade() -> None:
    op.drop_index("ix_document_sign_sessions_token_hash", table_name="document_sign_sessions")
    op.drop_index("ix_document_sign_sessions_expires_at", table_name="document_sign_sessions")
    op.drop_index("ix_document_sign_sessions_document_id", table_name="document_sign_sessions")
    op.drop_table("document_sign_sessions")

    bind = op.get_bind()
    dialect_name = bind.dialect.name.lower()
    if not dialect_name.startswith("sqlite"):
        op.drop_constraint("fk_documents_company_id_to_users", "documents", type_="foreignkey")
        op.drop_constraint("fk_documents_company_id_from_users", "documents", type_="foreignkey")
    op.drop_index("ix_documents_company_id_to", table_name="documents")
    op.drop_index("ix_documents_company_id_from", table_name="documents")

    op.drop_column("documents", "updated_at")
    op.drop_column("documents", "pdf_signed_path")
    op.drop_column("documents", "pdf_draft_path")
    op.drop_column("documents", "payload_json")
    op.drop_column("documents", "company_id_to")
    op.drop_column("documents", "company_id_from")
