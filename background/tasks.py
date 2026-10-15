from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from aiogram import Bot
from celery import Task
from sqlalchemy import select

import config
from background.celery_app import celery_app
from background.task_db import (
    append_task_log,
    as_utc,
    attach_celery_task_id,
    create_task_run,
    ensure_database,
    fail_task_run,
    finish_task_run,
    start_task_run,
    utc_now,
)
from bot.config import BotConfig
from bot.db.models import UserFavourite
from bot.db.session import get_session_factory as get_bot_session_factory
from bot.db.session import init_engine as init_bot_engine
from bot.services.availability_service import AvailabilityService
from bot.services.notification_service import NotificationService
from kufar_scraper.db.client import (
    archive_unseen_from_completed_reconciliation,
    get_active_cities,
    get_session,
    upsert_listing,
)
from kufar_scraper.db.models import CityConfigDB, Listing, TaskRun, TaskSchedule
from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.client import KnownAdState, KufarClient
from kufar_scraper.scraper.parser import parse_ad

logger = logging.getLogger(__name__)

TaskHandler = Callable[[int], Awaitable[dict[str, Any]]]
_task_loop: asyncio.AbstractEventLoop | None = None


def _sync(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def _task_log(run_id: int, message: str, level: str = "info") -> None:
    await append_task_log(run_id, message, level)


async def _run_tracked(
    celery_task: Task,
    task_name: str,
    handler: TaskHandler,
    *,
    run_id: int | None = None,
    mode: str | None = None,
    city_name: str | None = None,
) -> dict[str, Any]:
    await ensure_database()
    celery_task_id = celery_task.request.id
    if run_id is None:
        run_id = await create_task_run(task_name, celery_task_id=celery_task_id, mode=mode, city_name=city_name)
    else:
        await attach_celery_task_id(run_id, celery_task_id)

    await start_task_run(run_id, celery_task_id)
    await _task_log(run_id, f"Task started: {task_name}")
    counters: dict[str, Any] = {}
    try:
        counters = await handler(run_id)
        await finish_task_run(run_id, counters)
        await _task_log(run_id, f"Task finished: {task_name}")
        return {"run_id": run_id, "counters": counters}
    except Exception as exc:
        counters = getattr(exc, "counters", counters)
        await fail_task_run(run_id, str(exc), counters)
        await _task_log(run_id, f"Task failed: {exc}", "error")
        raise


async def _get_cities(city_name: str | None = None) -> list[CityConfig]:
    async with get_session() as session:
        if city_name:
            city = (
                await session.execute(
                    select(CityConfigDB).where(CityConfigDB.name == city_name, CityConfigDB.enabled.is_(True))
                )
            ).scalar_one_or_none()
            return [CityConfig(name=city.name, rgn=city.rgn, ar=city.ar)] if city else []
        return await get_active_cities(session)


class ScrapeFailure(RuntimeError):
    def __init__(self, message: str, counters: dict):
        super().__init__(message)
        self.counters = dict(counters)


async def _known_ad_states(city_name: str) -> dict[int, KnownAdState]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Listing.ad_id, Listing.list_time, Listing.raw_search, Listing.details_fetched_at).where(
                    Listing.city == city_name
                )
            )
        ).all()
        return {
            row.ad_id: KnownAdState(row.list_time, row.raw_search or {}, row.details_fetched_at is not None)
            for row in rows
        }


def _counters(cities: int) -> dict[str, Any]:
    return {
        "cities": cities,
        "pages": 0,
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "detail_failures": 0,
        "undated": 0,
        "stopped_on_existing_pages": 0,
        "known_touched": 0,
        "source_archived": 0,
        "full_fallbacks": 0,
        "source_totals": {},
    }


async def _save_raw_ads(raw_ads: list[dict[str, Any]], run_id: int) -> dict[str, int]:
    counters = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0, "detail_failures": 0, "undated": 0}
    async with get_session() as session:
        for raw in raw_ads:
            try:
                parsed = parse_ad(raw)
                async with session.begin_nested():
                    action = await upsert_listing(session, parsed, commit=False)
                counters[action] += 1
                counters["detail_failures"] += int(bool(raw.get("_detail_failed")))
                counters["undated"] += int(parsed.list_time is None)
            except Exception as exc:
                counters["errors"] += 1
                await _task_log(run_id, f"Failed to process listing {raw.get('ad_id')}: {exc}", "warning")
        await session.commit()
    if counters["inserted"]:
        try:
            process_listing_notifications.apply_async(queue="bot")
        except Exception:
            logger.warning("Immediate feed wake-up failed; periodic scan will retry.")
    return counters


def _stream_saver(run_id, counters):
    saved = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0, "detail_failures": 0, "undated": 0}

    async def save_page(ads):
        result = await _save_raw_ads(ads, run_id)
        for key, value in result.items():
            saved[key] += value
            counters[key] += value

    return saved, save_page


async def _full_city(client: KufarClient, city: CityConfig, run_id: int, counters: dict) -> None:
    started_at = utc_now()
    await _task_log(run_id, f"[{city.name}] Full reconciliation started; enriching all source fields.")
    saved, save_page = _stream_saver(run_id, counters)
    fetched = await client.fetch_all_ads(
        city,
        max_pages=config.FULL_RECONCILIATION_MAX_PAGES,
        ads_callback=save_page,
        log_callback=lambda message: asyncio.create_task(_task_log(run_id, message)),
    )
    counters["pages"] += fetched.pages
    counters["fetched"] += fetched.fetched
    counters["source_totals"][city.name] = fetched.source_total
    if not fetched.completed or saved["errors"]:
        raise ScrapeFailure(
            f"[{city.name}] reconciliation incomplete; observed data saved, archiving skipped: {fetched.error or 'listing parse/storage errors'}",
            counters,
        )
    async with get_session() as session:
        archived = await archive_unseen_from_completed_reconciliation(session, city.name, started_at)
        row = (await session.execute(select(CityConfigDB).where(CityConfigDB.name == city.name))).scalar_one()
        row.catalog_initialized_at = utc_now()
        row.availability_checked_at = row.catalog_initialized_at
        await session.commit()
    counters["source_archived"] += archived
    await _task_log(
        run_id,
        f"[{city.name}] Complete: source total={fetched.source_total}, fetched={fetched.fetched}, inserted={saved['inserted']}, changed={saved['updated']}, unchanged={saved['unchanged']}, archived={archived}.",
    )


async def _incremental_scrape(run_id: int, city_name: str | None = None) -> dict[str, Any]:
    cities = await _get_cities(city_name)
    counters = _counters(len(cities))
    async with get_session() as session:
        schedule = await session.get(TaskSchedule, "new_listings")
        max_pages = schedule.max_pages if schedule and schedule.max_pages else config.NEW_LISTINGS_MAX_PAGES
    counters["max_pages"] = max_pages
    counters["page_limit_reached"] = 0
    async with KufarClient() as client:
        for city in cities:
            states = await _known_ad_states(city.name)
            saved, save_page = _stream_saver(run_id, counters)
            result = await client.fetch_new_ads(
                city,
                states,
                max_pages=max_pages,
                stop_on_known=False,
                ads_callback=save_page,
                known_page_overlap=config.NEW_LISTINGS_KNOWN_PAGE_OVERLAP,
                log_callback=lambda message: asyncio.create_task(_task_log(run_id, message)),
            )
            counters["pages"] += result.pages
            counters["fetched"] += result.fetched
            counters["source_totals"][city.name] = result.source_total
            counters["stopped_on_existing_pages"] += int(result.stopped_on_existing_page)
            counters["known_touched"] += len(result.known_ids_seen)
            if result.hit_page_limit:
                counters["page_limit_reached"] += 1
                await _task_log(
                    run_id,
                    f"[{city.name}] Quick scan finished at the configured {max_pages}-page budget; full refresh runs independently.",
                )
            if (result.error and not result.hit_page_limit) or saved["errors"]:
                raise ScrapeFailure(
                    f"[{city.name}] incremental incomplete: {result.error or 'listing parse/storage errors'}", counters
                )
            await _task_log(
                run_id,
                f"[{city.name}] Updated source snapshots; source total={result.source_total}, new={saved['inserted']}, changed={saved['updated']}, unchanged={saved['unchanged']}.",
            )
    return counters


async def _full_reconciliation(run_id: int, city_name: str | None = None) -> dict[str, Any]:
    cities = await _get_cities(city_name)
    counters = _counters(len(cities))
    async with KufarClient() as client:
        for city in cities:
            await _full_city(client, city, run_id, counters)
    return counters


@celery_app.task(bind=True, name="background.tasks.load_new_listings")
def load_new_listings(self: Task, run_id: int | None = None, city_name: str | None = None) -> dict[str, Any]:
    return _sync(
        _run_tracked(
            self,
            "load_new_listings",
            lambda task_run_id: _incremental_scrape(task_run_id, city_name),
            run_id=run_id,
            mode="incremental",
            city_name=city_name,
        )
    )


@celery_app.task(bind=True, name="background.tasks.full_scrape")
def full_scrape(self: Task, run_id: int | None = None, city_name: str | None = None) -> dict[str, Any]:
    return _sync(
        _run_tracked(
            self,
            "full_scrape",
            lambda task_run_id: _full_reconciliation(task_run_id, city_name),
            run_id=run_id,
            mode="reconciliation",
            city_name=city_name,
        )
    )


@celery_app.task(bind=True, name="background.tasks.process_listing_notifications")
def process_listing_notifications(self: Task, run_id: int | None = None) -> dict[str, Any]:
    async def handler(tracked_run_id: int) -> dict[str, Any]:
        bot_config = BotConfig.from_env()
        init_bot_engine(bot_config.database_url)
        bot = Bot(token=bot_config.bot_token)
        try:
            async with get_bot_session_factory()() as session:
                service = NotificationService(session, bot)
                counters = await service.run_cycle()
                await _task_log(tracked_run_id, f"Notifications: {counters}")
                return counters
        finally:
            await bot.session.close()

    return _sync(_run_tracked(self, "process_listing_notifications", handler, run_id=run_id, mode="outbox"))


@celery_app.task(bind=True, name="background.tasks.check_favourite_availability")
def check_favourite_availability(self, run_id=None):
    async def handler(tracked_run_id):
        async with get_session() as session:
            cities = list(
                (
                    await session.scalars(
                        select(Listing.city).join(UserFavourite, UserFavourite.ad_id == Listing.ad_id).distinct()
                    )
                ).all()
            )
        counters = {"cities": len(cities), "checked": 0, "failed": 0}
        service = AvailabilityService(get_session)
        for city in cities:
            try:
                result = await service.check_city(city, max_age_seconds=config.AVAILABILITY_CHECK_INTERVAL_SECONDS)
            except Exception as exc:
                result = "unavailable"
                await _task_log(tracked_run_id, f"Availability check failed: {city}: {type(exc).__name__}", "warning")
            counters["checked"] += int(result == "checked")
            counters["failed"] += int(result == "unavailable")
        return counters

    return _sync(_run_tracked(self, "check_favourite_availability", handler, run_id=run_id, mode="availability"))


_SCHEDULED_TASKS = {
    "new_listings": (load_new_listings, "load_new_listings", "incremental"),
    "full_reconciliation": (full_scrape, "full_scrape", "reconciliation"),
}


@celery_app.task(name="background.tasks.scheduler_tick")
def scheduler_tick() -> dict[str, Any]:
    return _sync(_scheduler_tick())


async def _scheduler_tick() -> dict[str, Any]:
    await ensure_database()
    now = utc_now()
    counters: dict[str, Any] = {"checked": 0, "enqueued": 0, "runs": []}
    queued: list[tuple[Any, int]] = []

    async with get_session() as session:
        schedules = (
            (
                await session.execute(
                    select(TaskSchedule).where(TaskSchedule.key.in_(_SCHEDULED_TASKS)).with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for schedule in schedules:
            counters["checked"] += 1
            if not schedule.enabled or (as_utc(schedule.next_run_at) and as_utc(schedule.next_run_at) > now):
                continue
            task, task_name, mode = _SCHEDULED_TASKS[schedule.key]
            existing = await session.scalar(
                select(TaskRun.id)
                .where(TaskRun.task_name == task_name, TaskRun.status.in_(["queued", "running"]))
                .limit(1)
            )
            if existing is not None:
                continue
            run = TaskRun(task_name=task_name, status="queued", mode=mode, counters={})
            session.add(run)
            await session.flush()
            schedule.last_enqueued_at = now
            schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
            queued.append((task, run.id))
        await session.commit()

    for task, run_id in queued:
        result = task.apply_async(kwargs={"run_id": run_id}, queue="scraper")
        await attach_celery_task_id(run_id, result.id)
        counters["runs"].append(run_id)
    counters["enqueued"] = len(queued)
    return counters
