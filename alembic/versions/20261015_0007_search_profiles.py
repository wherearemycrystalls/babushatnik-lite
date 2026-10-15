"""Multiple named search profiles and delivery provenance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261015_0007"
down_revision = "20261015_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("user_preferences_telegram_id_key", "user_preferences", type_="unique")
    op.add_column("user_preferences", sa.Column("name", sa.String(40), nullable=False, server_default="Основной"))
    op.add_column("user_preferences", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("user_preferences", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE user_preferences p SET started_at=COALESCE(u.notification_started_at,u.notification_cursor_at,now()) FROM bot_users u WHERE u.telegram_id=p.telegram_id")
    op.create_unique_constraint("uq_search_profile_user_name", "user_preferences", ["telegram_id", "name"])
    op.add_column("listing_deliveries", sa.Column("matched_profiles", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade():
    # Refuse a lossy rollback when a user owns multiple profiles.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM user_preferences GROUP BY telegram_id HAVING count(*)>1) THEN RAISE EXCEPTION 'Multiple profiles exist; restore a backup for rollback'; END IF; END $$")
    op.drop_column("listing_deliveries", "matched_profiles")
    op.drop_constraint("uq_search_profile_user_name", "user_preferences", type_="unique")
    for name in ["started_at", "enabled", "name"]:
        op.drop_column("user_preferences", name)
    op.create_unique_constraint("user_preferences_telegram_id_key", "user_preferences", ["telegram_id"])
