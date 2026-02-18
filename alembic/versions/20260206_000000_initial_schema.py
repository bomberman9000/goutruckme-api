"""Initial schema: users, trucks, loads, bids, messages, rating_history, complaints, forum.

Revision ID: 20260206_000000
Revises:
Create Date: 2026-02-06

Используется на пустой БД (нет таблиц). Следующая миграция: e842bea3c69d (telegram, deals, documents).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260206_000000"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users (role как VARCHAR для совместимости с SQLite и Postgres)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fullname", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("organization_type", sa.String(), nullable=True),
        sa.Column("inn", sa.String(), nullable=True),
        sa.Column("organization_name", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("bank_name", sa.String(), nullable=True),
        sa.Column("bank_account", sa.String(), nullable=True),
        sa.Column("bank_bik", sa.String(), nullable=True),
        sa.Column("bank_ks", sa.String(), nullable=True),
        sa.Column("payment_confirmed", sa.Boolean(), nullable=True),
        sa.Column("payment_date", sa.DateTime(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("successful_deals", sa.Integer(), nullable=True),
        sa.Column("complaints", sa.Integer(), nullable=True),
        sa.Column("disputes", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("trust_level", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_inn"), "users", ["inn"], unique=True)
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)

    # trucks
    op.create_table(
        "trucks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("capacity", sa.Float(), nullable=True),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trucks_id"), "trucks", ["id"], unique=False)

    # loads
    op.create_table(
        "loads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("from_city", sa.String(), nullable=False),
        sa.Column("to_city", sa.String(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_loads_id"), "loads", ["id"], unique=False)

    # bids
    op.create_table(
        "bids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("load_id", sa.Integer(), nullable=True),
        sa.Column("carrier_id", sa.Integer(), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["carrier_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bids_id"), "bids", ["id"], unique=False)

    # messages
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("load_id", sa.Integer(), nullable=True),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)

    # rating_history
    op.create_table(
        "rating_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("points_change", sa.Integer(), nullable=True),
        sa.Column("rating_before", sa.Float(), nullable=True),
        sa.Column("rating_after", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("load_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_rating_history_id"), "rating_history", ["id"], unique=False)

    # complaints
    op.create_table(
        "complaints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("complainant_id", sa.Integer(), nullable=True),
        sa.Column("defendant_id", sa.Integer(), nullable=True),
        sa.Column("load_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("complaint_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["complainant_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["defendant_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_complaints_id"), "complaints", ["id"], unique=False)

    # forum_posts
    op.create_table(
        "forum_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("post_type", sa.String(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("target_company", sa.String(), nullable=True),
        sa.Column("target_phone", sa.String(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("dislikes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_forum_posts_id"), "forum_posts", ["id"], unique=False)

    # forum_comments
    op.create_table(
        "forum_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["forum_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_forum_comments_id"), "forum_comments", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_forum_comments_id"), table_name="forum_comments")
    op.drop_table("forum_comments")
    op.drop_index(op.f("ix_forum_posts_id"), table_name="forum_posts")
    op.drop_table("forum_posts")
    op.drop_index(op.f("ix_complaints_id"), table_name="complaints")
    op.drop_table("complaints")
    op.drop_index(op.f("ix_rating_history_id"), table_name="rating_history")
    op.drop_table("rating_history")
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_bids_id"), table_name="bids")
    op.drop_table("bids")
    op.drop_index(op.f("ix_loads_id"), table_name="loads")
    op.drop_table("loads")
    op.drop_index(op.f("ix_trucks_id"), table_name="trucks")
    op.drop_table("trucks")
    op.drop_index(op.f("ix_users_phone"), table_name="users")
    op.drop_index(op.f("ix_users_inn"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
