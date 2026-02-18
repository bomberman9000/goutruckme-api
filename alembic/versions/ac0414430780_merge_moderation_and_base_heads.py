"""merge moderation and base heads

Revision ID: ac0414430780
Revises: 20260205_ae_actor, 20260212_mod
Create Date: 2026-02-12 13:09:58.930459

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac0414430780'
down_revision: Union[str, None] = ('20260205_ae_actor', '20260212_mod')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass




