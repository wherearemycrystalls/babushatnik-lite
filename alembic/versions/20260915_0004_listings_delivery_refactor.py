"""remove ML and add listing lifecycle plus notification outbox

Revision ID: 20260915_0004
Revises: 20260810_0003
Create Date: 2026-09-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260915_0004"
down_revision = "20260810_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listings", sa.Column("is_source_active", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("listings", sa.Column("source_inactive_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listings", sa.Column("is_hidden_by_admin", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("listings", sa.Column("manual_archived_at", sa.DateTime(timezone=True), nullable=True))
    # Existing rows have unknown source status. They remain archived until a new scrape sees them.
    op.execute("UPDATE listings SET last_seen_at = updated_at, is_source_active = FALSE")
    op.alter_column("listings", "is_source_active", server_default=None)
    op.alter_column("listings", "is_hidden_by_admin", server_default=None)
    op.create_index("ix_listings_last_seen_at", "listings", ["last_seen_at"])
    op.create_index("ix_listings_is_source_active", "listings", ["is_source_active"])
    op.create_index("ix_listings_is_hidden_by_admin", "listings", ["is_hidden_by_admin"])
    op.create_index(
        "ix_listings_visible_match",
        "listings",
        ["city", "rooms", "price_usd", "created_at"],
        postgresql_where=sa.text("is_source_active AND NOT is_hidden_by_admin"),
    )
    op.drop_column("listings", "price_delta")
    op.drop_column("listings", "distance_to_center")
    op.drop_column("listings", "renovation_type")

    op.drop_table("evaluation_logs")

    op.drop_column("user_preferences", "price_delta_min")
    op.drop_column("user_preferences", "distance_max_km")
    op.drop_column("user_preferences", "renovation_type")

    op.add_column("bot_users", sa.Column("notification_cursor_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE bot_users SET notification_cursor_at = notification_last_checked_at")
    op.drop_column("bot_users", "notification_last_checked_at")
    op.drop_table("user_notified")

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_deliveries_telegram_id", "notification_deliveries", ["telegram_id"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_index("ix_notification_deliveries_next_attempt_at", "notification_deliveries", ["next_attempt_at"])
    op.execute(
        "CREATE UNIQUE INDEX uq_notification_deliveries_open_per_user "
        "ON notification_deliveries (telegram_id) WHERE status IN ('pending', 'processing')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_notification_deliveries_open_per_user")
    op.drop_index("ix_notification_deliveries_next_attempt_at", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_telegram_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    # The intentionally removed ML data is not reconstructable on downgrade.
    op.add_column("bot_users", sa.Column("notification_last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE bot_users SET notification_last_checked_at = notification_cursor_at")
    op.drop_column("bot_users", "notification_cursor_at")
    op.add_column("user_preferences", sa.Column("renovation_type", sa.SmallInteger(), nullable=True))
    op.add_column("user_preferences", sa.Column("distance_max_km", sa.Float(), nullable=True))
    op.add_column("user_preferences", sa.Column("price_delta_min", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("renovation_type", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("distance_to_center", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("price_delta", sa.Float(), nullable=True))
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
        "user_notified",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ad_id"], ["listings.ad_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("telegram_id", "ad_id"),
    )
    op.drop_index("ix_listings_visible_match", table_name="listings")
    op.drop_index("ix_listings_is_hidden_by_admin", table_name="listings")
    op.drop_index("ix_listings_is_source_active", table_name="listings")
    op.drop_index("ix_listings_last_seen_at", table_name="listings")
    op.drop_column("listings", "manual_archived_at")
    op.drop_column("listings", "is_hidden_by_admin")
    op.drop_column("listings", "source_inactive_at")
    op.drop_column("listings", "is_source_active")
    op.drop_column("listings", "last_seen_at")
