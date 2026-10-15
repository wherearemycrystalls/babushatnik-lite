"""Replace count digests with an individual apartment outbox."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0006"
down_revision = "20261015_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("listing_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), sa.ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), sa.ForeignKey("listings.ad_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("telegram_id", "ad_id", name="uq_listing_delivery_user_ad"),
    )
    op.create_index("ix_listing_deliveries_due", "listing_deliveries", ["status", "next_attempt_at"])
    op.add_column("city_configs", sa.Column("availability_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE notification_deliveries SET status='cancelled', lease_expires_at=NULL WHERE status IN ('pending','processing')")
    # Do not turn the existing catalog into a backlog on rollout; retain subscription start.
    op.execute("UPDATE bot_users SET notification_cursor_at=now(), notification_started_at=COALESCE(notification_started_at,notification_cursor_at,now())")
    op.execute("UPDATE task_schedules SET interval_seconds=60, next_run_at=now() WHERE key='new_listings'")


def downgrade():
    op.drop_column("city_configs", "availability_checked_at")
    op.drop_table("listing_deliveries")
