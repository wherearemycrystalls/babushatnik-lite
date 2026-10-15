"""add user notified table

Revision ID: 20260810_0003
Revises: 20260810_0002
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260810_0003"
down_revision = "20260810_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notified",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ad_id"], ["listings.ad_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["telegram_id"], ["bot_users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("telegram_id", "ad_id"),
    )


def downgrade() -> None:
    op.drop_table("user_notified")
