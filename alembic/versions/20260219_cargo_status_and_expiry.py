"""cargo status + loading_date + auto-expire preparation

Revision ID: 20260219_cargo_status
Revises: 20260219_shipments
Create Date: 2026-02-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260219_cargo_status"
down_revision: Union[str, None] = "20260219_shipments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("loads")}
    if "loading_date" not in existing_columns:
        with op.batch_alter_table("loads") as batch_op:
            batch_op.add_column(sa.Column("loading_date", sa.Date(), nullable=True))

    # Legacy statuses -> canonical statuses.
    op.execute("UPDATE loads SET status = 'active' WHERE status IS NULL OR TRIM(status) = ''")
    op.execute("UPDATE loads SET status = 'active' WHERE LOWER(status) = 'open'")
    op.execute("UPDATE loads SET status = 'closed' WHERE LOWER(status) = 'covered'")
    op.execute("UPDATE loads SET status = 'cancelled' WHERE LOWER(status) = 'canceled'")

    # Для исторических записей берём loading_date из created_at.
    op.execute(
        "UPDATE loads SET loading_date = DATE(created_at) "
        "WHERE loading_date IS NULL AND created_at IS NOT NULL"
    )

    # Истёкшие грузы.
    op.execute(
        "UPDATE loads SET status = 'expired' "
        "WHERE status = 'active' AND loading_date IS NOT NULL AND loading_date < CURRENT_DATE"
    )

    with op.batch_alter_table("loads") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(),
            nullable=False,
            server_default="active",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_status ON loads (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_loading_date ON loads (loading_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_loads_status_loading_date ON loads (status, loading_date)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_loads_status_loading_date")
    op.execute("DROP INDEX IF EXISTS ix_loads_loading_date")
    op.execute("DROP INDEX IF EXISTS ix_loads_status")

    # Partial reverse mapping for compatibility with old flows.
    op.execute("UPDATE loads SET status = 'open' WHERE status IN ('active', 'expired')")
    op.execute("UPDATE loads SET status = 'covered' WHERE status = 'closed'")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("loads")}

    with op.batch_alter_table("loads") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(),
            nullable=True,
            server_default=None,
        )
        if "loading_date" in existing_columns:
            batch_op.drop_column("loading_date")
