"""Persist quick-search page budget alongside its schedule."""

from alembic import op
import sqlalchemy as sa

revision = "20261015_0008"
down_revision = "20261015_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_schedules", sa.Column("max_pages", sa.Integer(), nullable=True))
    # Preserve the configured environment default on existing deployments.
    import config

    pages = max(1, config.NEW_LISTINGS_MAX_PAGES)
    op.execute(
        sa.text(
            "UPDATE task_schedules SET max_pages=:pages WHERE key='new_listings'"
        ).bindparams(pages=pages)
    )
    op.create_check_constraint(
        "ck_task_schedule_max_pages_positive",
        "task_schedules",
        "max_pages IS NULL OR max_pages > 0",
    )


def downgrade():
    op.drop_constraint(
        "ck_task_schedule_max_pages_positive", "task_schedules", type_="check"
    )
    op.drop_column("task_schedules", "max_pages")
