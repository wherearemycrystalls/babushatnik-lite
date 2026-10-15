from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

import config
from kufar_scraper.db.client import (
    get_session,
    init_engine,
    seed_default_cities,
    seed_default_task_schedules,
)
from kufar_scraper.db.models import TaskLog, TaskRun

_database_ready = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def ensure_database() -> None:
    """Initialise connections and configuration only; Alembic owns DDL."""
    global _database_ready
    if _database_ready:
        return
    init_engine(config.DATABASE_URL)
    async with get_session() as session:
        await seed_default_cities(session, config.CITIES)
        await seed_default_task_schedules(
            session,
            new_listings_interval_seconds=config.NEW_LISTINGS_DEFAULT_INTERVAL_SECONDS,
            full_reconciliation_interval_seconds=config.FULL_RECONCILIATION_INTERVAL_SECONDS,
            new_listings_max_pages=config.NEW_LISTINGS_MAX_PAGES,
        )
    _database_ready = True


async def create_task_run(
    task_name: str,
    *,
    celery_task_id: str | None = None,
    status: str = "queued",
    mode: str | None = None,
    city_name: str | None = None,
    counters: dict[str, Any] | None = None,
) -> int:
    await ensure_database()
    async with get_session() as session:
        run = TaskRun(
            task_name=task_name,
            celery_task_id=celery_task_id,
            status=status,
            mode=mode,
            city_name=city_name,
            counters=counters or {},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


async def attach_celery_task_id(run_id: int, celery_task_id: str) -> None:
    await ensure_database()
    async with get_session() as session:
        run = await session.get(TaskRun, run_id)
        if run:
            run.celery_task_id = celery_task_id
            await session.commit()


async def start_task_run(run_id: int, celery_task_id: str | None = None) -> None:
    await ensure_database()
    async with get_session() as session:
        run = await session.get(TaskRun, run_id)
        if run:
            run.status = "running"
            run.started_at = utc_now()
            if celery_task_id:
                run.celery_task_id = celery_task_id
            await session.commit()


async def finish_task_run(run_id: int, counters: dict[str, Any] | None = None) -> None:
    await ensure_database()
    async with get_session() as session:
        run = await session.get(TaskRun, run_id)
        if run:
            run.status = "success"
            run.finished_at = utc_now()
            if counters is not None:
                run.counters = counters
            await session.commit()


async def fail_task_run(
    run_id: int,
    error: str,
    counters: dict[str, Any] | None = None,
) -> None:
    await ensure_database()
    async with get_session() as session:
        run = await session.get(TaskRun, run_id)
        if run:
            run.status = "error"
            run.finished_at = utc_now()
            run.error = error
            if counters is not None:
                run.counters = counters
            await session.commit()


async def append_task_log(run_id: int, message: str, level: str = "info") -> None:
    await ensure_database()
    async with get_session() as session:
        exists = await session.scalar(select(TaskRun.id).where(TaskRun.id == run_id))
        if exists:
            session.add(TaskLog(run_id=run_id, level=level, message=message))
            await session.commit()
