"""add per-user notification interval

Revision ID: 20260810_0002
Revises: 20260810_0001
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260810_0002"
down_revision = "20260810_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_users",
        sa.Column("notification_interval_minutes", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "bot_users",
        sa.Column("notification_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_users", "notification_last_checked_at")
    op.drop_column("bot_users", "notification_interval_minutes")
