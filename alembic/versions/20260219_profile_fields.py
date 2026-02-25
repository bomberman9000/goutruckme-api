"""add profile fields to users table

Revision ID: 20260219_profile
Revises: 20260218_trust
Create Date: 2026-02-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260219_profile"
down_revision: Union[str, None] = "20260218_trust"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.add_column("users", sa.Column("ogrn", sa.String(), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(), nullable=True))
    op.add_column("users", sa.Column("contact_person", sa.String(), nullable=True))
    op.add_column("users", sa.Column("website", sa.String(), nullable=True))
    op.add_column("users", sa.Column("edo_enabled", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("users", sa.Column("requisites_verified", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("users", sa.Column("documents_verified", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.create_index("ix_users_ogrn", "users", ["ogrn"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_ogrn", table_name="users")
    op.drop_column("users", "documents_verified")
    op.drop_column("users", "requisites_verified")
    op.drop_column("users", "edo_enabled")
    op.drop_column("users", "website")
    op.drop_column("users", "contact_person")
    op.drop_column("users", "city")
    op.drop_column("users", "ogrn")
    op.drop_column("users", "email")
