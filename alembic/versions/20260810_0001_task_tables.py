"""add task schedule and run tables

Revision ID: 20260810_0001
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260810_0001"
down_revision = "20260810_0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_schedules",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=True),
        sa.Column("city_name", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_runs_celery_task_id", "task_runs", ["celery_task_id"])
    op.create_index("ix_task_runs_created_at", "task_runs", ["created_at"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])

    op.create_table(
        "task_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_logs_created_at", "task_logs", ["created_at"])
    op.create_index("ix_task_logs_run_id", "task_logs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_task_logs_run_id", table_name="task_logs")
    op.drop_index("ix_task_logs_created_at", table_name="task_logs")
    op.drop_table("task_logs")

    op.drop_index("ix_task_runs_task_name", table_name="task_runs")
    op.drop_index("ix_task_runs_status", table_name="task_runs")
    op.drop_index("ix_task_runs_created_at", table_name="task_runs")
    op.drop_index("ix_task_runs_celery_task_id", table_name="task_runs")
    op.drop_table("task_runs")

    op.drop_table("task_schedules")
