"""Preserve Kufar source time, payloads, apartment metadata and observed changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261015_0005"
down_revision = "20260915_0004"
branch_labels = None
depends_on = None

TIME_COLUMNS = ["list_time", "first_list_time", "source_observed_at", "details_fetched_at", "content_changed_at"]
TEXT_COLUMNS = ["title", "repair", "house_type", "bathroom", "balcony", "prepayment"]
STRING_COLUMNS = {"currency": 10, "category": 50, "ad_type": 50, "remuneration_type": 50}
NUMERIC_COLUMNS = {"price_eur": (14, 2), "area_total": (12, 2), "area_living": (12, 2), "area_kitchen": (12, 2), "longitude": (11, 8), "latitude": (11, 8), "distance_to_center_km": (10, 3)}
JSON_COLUMNS = {"parameter_details": "{}", "raw_search": "{}", "data_warnings": "[]"}


def upgrade() -> None:
    for column in ["price_byn", "price_usd"]:
        op.alter_column("listings", column, type_=sa.Numeric(14, 2), postgresql_using=f"{column}::numeric(14,2)")
    for column in TIME_COLUMNS:
        op.add_column("listings", sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    for column in TEXT_COLUMNS:
        op.add_column("listings", sa.Column(column, sa.Text(), nullable=True))
    for column, length in STRING_COLUMNS.items():
        op.add_column("listings", sa.Column(column, sa.String(length), nullable=True))
    for column, (precision, scale) in NUMERIC_COLUMNS.items():
        op.add_column("listings", sa.Column(column, sa.Numeric(precision, scale), nullable=True))
    for column in ["account_id", "list_id"]:
        op.add_column("listings", sa.Column(column, sa.BigInteger(), nullable=True))
    for column in ["total_floors", "year_built"]:
        op.add_column("listings", sa.Column(column, sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("company_ad", sa.Boolean(), nullable=True))
    for column, default in JSON_COLUMNS.items():
        op.add_column("listings", sa.Column(column, postgresql.JSONB(), server_default=sa.text(f"'{default}'::jsonb"), nullable=False))
    op.add_column("listings", sa.Column("raw_detail", postgresql.JSONB(), nullable=True))
    op.add_column("city_configs", sa.Column("catalog_initialized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bot_users", sa.Column("notification_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE bot_users SET notification_started_at = COALESCE(notification_cursor_at, now())")
    # An incomplete legacy catalog must be reconciled instead of waiting another day.
    op.execute("UPDATE task_schedules SET next_run_at = now() WHERE key = 'full_reconciliation' AND enabled")
    op.create_index("ix_listings_list_time", "listings", ["list_time"])
    op.create_index("ix_listings_source_order", "listings", ["city", sa.text("list_time DESC NULLS LAST"), sa.text("ad_id DESC")], postgresql_where=sa.text("is_source_active AND NOT is_hidden_by_admin"))
    op.create_table("listing_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_id", sa.BigInteger(), sa.ForeignKey("listings.ad_id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_listing_changes_ad_id", "listing_changes", ["ad_id"])


def downgrade() -> None:
    op.drop_table("listing_changes")
    op.drop_index("ix_listings_source_order", table_name="listings")
    op.drop_index("ix_listings_list_time", table_name="listings")
    op.drop_column("bot_users", "notification_started_at")
    op.drop_column("city_configs", "catalog_initialized_at")
    columns = TIME_COLUMNS + TEXT_COLUMNS + list(STRING_COLUMNS) + list(NUMERIC_COLUMNS) + list(JSON_COLUMNS) + ["account_id", "list_id", "total_floors", "year_built", "company_ad", "raw_detail"]
    for column in reversed(columns):
        op.drop_column("listings", column)
    for column in ["price_byn", "price_usd"]:
        op.alter_column("listings", column, type_=sa.Integer(), postgresql_using=f"trunc({column})::integer")
