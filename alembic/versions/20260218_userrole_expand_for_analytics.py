"""expand userrole enum for analytics roles

Revision ID: 20260218_userrole
Revises: ac0414430780
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260218_userrole"
down_revision: Union[str, None] = "ac0414430780"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'client'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'forwarder'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'expeditor'")


def downgrade() -> None:
    # PostgreSQL не поддерживает удаление значений ENUM без сложной миграции
    # и переписывания столбца; для безопасного MVP делаем no-op.
    pass
