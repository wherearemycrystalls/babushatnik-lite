"""baseline schema for clean installations

Revision ID: 20260810_0000
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260810_0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "city_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("rgn", sa.String(length=20), nullable=False),
        sa.Column("ar", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "listings",
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("city", sa.String(length=50), nullable=False),
        sa.Column("price_byn", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Integer(), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("rooms", sa.String(length=50), nullable=True),
        sa.Column("floor", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("renovation_type", sa.Integer(), nullable=True),
        sa.Column("distance_to_center", sa.Float(), nullable=True),
        sa.Column("price_delta", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("ad_id"),
    )
    op.create_index("ix_listings_city", "listings", ["city"])
    op.create_table(
        "evaluation_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_logs_ad_id", "evaluation_logs", ["ad_id"])
    op.create_table(
        "bot_users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("language_code", sa.String(length=8), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notifications", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id"),
    )
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("rooms", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("price_usd_min", sa.Integer(), nullable=True),
        sa.Column("price_usd_max", sa.Integer(), nullable=True),
        sa.Column("renovation_type", sa.SmallInteger(), nullable=True),
        sa.Column("distance_max_km", sa.Float(), nullable=True),
        sa.Column("price_delta_min", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_table(
        "user_favourites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ad_id"], ["listings.ad_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id", "ad_id"),
    )
    op.create_table(
        "user_seen",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ad_id"], ["listings.ad_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("telegram_id", "ad_id"),
    )


def downgrade() -> None:
    op.drop_table("user_seen")
    op.drop_table("user_favourites")
    op.drop_table("user_preferences")
    op.drop_table("bot_users")
    op.drop_index("ix_evaluation_logs_ad_id", table_name="evaluation_logs")
    op.drop_table("evaluation_logs")
    op.drop_index("ix_listings_city", table_name="listings")
    op.drop_table("listings")
    op.drop_table("city_configs")
