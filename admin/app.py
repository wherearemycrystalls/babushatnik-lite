import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select, text, true
from starlette.middleware.sessions import SessionMiddleware

import config
from admin.auth import SESSION_KEY, _AuthRedirect, require_auth, verify_telegram_data
from background.celery_app import celery_app
from background.task_db import as_utc, attach_celery_task_id, create_task_run, utc_now
from background.tasks import full_scrape, load_new_listings
from bot.utils.listing_info import number, parameter_value, source_time
from kufar_scraper.db.client import (
    get_session,
    init_engine,
    seed_default_cities,
    seed_default_task_schedules,
    set_manual_archive,
)
from kufar_scraper.db.models import (
    CityConfigDB,
    Listing,
    ListingChange,
    TaskLog,
    TaskRun,
    TaskSchedule,
)

logger = logging.getLogger(__name__)

PREFIX = "/admin"
PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine(config.DATABASE_URL)
    async with get_session() as session:
        await seed_default_cities(session, config.CITIES)
        await seed_default_task_schedules(
            session,
            new_listings_interval_seconds=config.NEW_LISTINGS_DEFAULT_INTERVAL_SECONDS,
            full_reconciliation_interval_seconds=config.FULL_RECONCILIATION_INTERVAL_SECONDS,
            new_listings_max_pages=config.NEW_LISTINGS_MAX_PAGES,
        )
    logger.info("Admin started. Background processing is managed through Celery tasks.")
    yield


app = FastAPI(lifespan=lifespan, title="Babushatnik Lite Admin")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET_KEY, max_age=86400 * 7)

templates = Jinja2Templates(directory="admin/templates")
templates.env.globals["prefix"] = PREFIX
templates.env.globals["telegram_auth_enabled"] = config.ADMIN_TELEGRAM_AUTH_ENABLED
templates.env.globals["display_timezone"] = config.DISPLAY_TIMEZONE
templates.env.filters.update(source_time=source_time, number=number, parameter_value=parameter_value)

router = APIRouter(prefix=PREFIX)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Return readiness only when the application can reach PostgreSQL."""
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth exception handler — catches _AuthRedirect from require_auth dependency
# ---------------------------------------------------------------------------


@app.exception_handler(_AuthRedirect)
async def auth_redirect_handler(request: Request, exc: _AuthRedirect):
    if exc.forbidden:
        return templates.TemplateResponse(request, "forbidden.html", status_code=403)
    return RedirectResponse(f"{PREFIX}/auth/login")


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@app.get("/")
async def root_redirect():
    return RedirectResponse(f"{PREFIX}/")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not config.ADMIN_TELEGRAM_AUTH_ENABLED:
        return RedirectResponse(f"{PREFIX}/", status_code=303)
    user = request.session.get(SESSION_KEY)
    if user:
        return RedirectResponse(f"{PREFIX}/")
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.get("/auth/telegram")
async def telegram_auth(request: Request):
    if not config.ADMIN_TELEGRAM_AUTH_ENABLED:
        return RedirectResponse(f"{PREFIX}/", status_code=303)
    data = dict(request.query_params)
    if not verify_telegram_data(data):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверные данные от Telegram. Попробуйте ещё раз."}, status_code=403
        )
    request.session[SESSION_KEY] = {
        "id": data["id"],
        "first_name": data.get("first_name", ""),
        "username": data.get("username", ""),
        "photo_url": data.get("photo_url", ""),
    }
    logger.info("Telegram auth: user_id=%s username=%s", data["id"], data.get("username"))
    return RedirectResponse(f"{PREFIX}/")


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"{PREFIX}/auth/login", status_code=303)


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

_SORT_MAP = {
    "price_byn": Listing.price_byn,
    "price_usd": Listing.price_usd,
    "rooms": Listing.rooms,
    "floor": Listing.floor,
    "created_at": Listing.created_at,
    "last_seen_at": Listing.last_seen_at,
    "updated_at": Listing.updated_at,
    "list_time": Listing.list_time,
    "area_total": Listing.area_total,
    "content_changed_at": Listing.content_changed_at,
}


@router.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    page: int = 1,
    city: str = "",
    rooms: str = "",
    min_price: str = "",
    max_price: str = "",
    status: str = "visible",
    sort: str = "list_time",
    listed_from: str = "",
    listed_to: str = "",
    min_area: str = "",
    max_area: str = "",
    seller: str = "",
    order: str = "desc",
    _user=Depends(require_auth),
):
    async with get_session() as session:
        filters = []
        if city:
            filters.append(Listing.city == city)
        if rooms:
            filters.append(Listing.rooms == rooms)
        for value, column, lower in [
            (min_price, Listing.price_usd, True),
            (max_price, Listing.price_usd, False),
            (min_area, Listing.area_total, True),
            (max_area, Listing.area_total, False),
        ]:
            if value:
                try:
                    numeric = Decimal(value)
                    if not numeric.is_finite() or numeric < 0:
                        raise ValueError
                except ValueError, InvalidOperation:
                    raise HTTPException(status_code=400, detail="Цена и площадь должны быть неотрицательными числами")
                filters.append(column >= numeric if lower else column <= numeric)
        for value, lower in [(listed_from, True), (listed_to, False)]:
            if value:
                try:
                    day = date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Некорректная дата")
                bound = datetime.combine(
                    day if lower else day + timedelta(days=1), time.min, tzinfo=ZoneInfo(config.DISPLAY_TIMEZONE)
                ).astimezone(timezone.utc)
                filters.append(Listing.list_time >= bound if lower else Listing.list_time < bound)
        if seller in {"company", "private"}:
            filters.append(Listing.company_ad.is_(seller == "company"))
        if status == "visible":
            filters += [Listing.is_source_active.is_(True), Listing.is_hidden_by_admin.is_(False)]
        elif status == "source_inactive":
            filters.append(Listing.is_source_active.is_(False))
        elif status == "hidden":
            filters.append(Listing.is_hidden_by_admin.is_(True))

        where = and_(*filters) if filters else true()

        sort_col = _SORT_MAP.get(sort, Listing.list_time)
        sort_expr = sort_col.asc().nulls_last() if order == "asc" else sort_col.desc().nulls_last()

        total: int = await session.scalar(select(func.count()).select_from(Listing).where(where))
        offset = (page - 1) * PAGE_SIZE
        rows = (
            (
                await session.execute(
                    select(Listing)
                    .where(where)
                    .order_by(sort_expr, Listing.ad_id.desc())
                    .offset(offset)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )

        city_names = (await session.execute(select(Listing.city).distinct().order_by(Listing.city))).scalars().all()

        rooms_list = (
            (
                await session.execute(
                    select(Listing.rooms).where(Listing.rooms.isnot(None)).distinct().order_by(Listing.rooms)
                )
            )
            .scalars()
            .all()
        )

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(
        request,
        "listings.html",
        {
            "listings": rows,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "city_names": city_names,
            "rooms_list": rooms_list,
            "f": {
                "city": city,
                "rooms": rooms,
                "min_price": min_price,
                "max_price": max_price,
                "status": status,
                "listed_from": listed_from,
                "listed_to": listed_to,
                "min_area": min_area,
                "max_area": max_area,
                "seller": seller,
            },
            "query_string": urlencode({key: value for key, value in request.query_params.items() if key != "page"}),
            "sort": sort,
            "order": order,
        },
    )


@router.get("/listings/{ad_id}", response_class=HTMLResponse)
async def listing_detail(ad_id: int, request: Request, _user=Depends(require_auth)):
    async with get_session() as session:
        listing = await session.get(Listing, ad_id)
        changes = list(
            (
                await session.execute(
                    select(ListingChange)
                    .where(ListingChange.ad_id == ad_id)
                    .order_by(ListingChange.observed_at.desc(), ListingChange.id.desc())
                    .limit(50)
                )
            ).scalars()
        )
    if listing is None:
        return RedirectResponse(f"{PREFIX}/", status_code=303)
    return templates.TemplateResponse(request, "listing_detail.html", {"listing": listing, "changes": changes})


@router.post("/listings/{ad_id}/archive")
async def archive_listing(ad_id: int, _user=Depends(require_auth)):
    async with get_session() as session:
        if await set_manual_archive(session, ad_id, True):
            await session.commit()
    return RedirectResponse(f"{PREFIX}/", status_code=303)


@router.post("/listings/{ad_id}/restore")
async def restore_listing(ad_id: int, _user=Depends(require_auth)):
    async with get_session() as session:
        if await set_manual_archive(session, ad_id, False):
            await session.commit()
    return RedirectResponse(f"{PREFIX}/", status_code=303)


@router.post("/listings/archive_by_city")
async def archive_listings_by_city(city: str = Form(...), _user=Depends(require_auth)):
    async with get_session() as session:
        rows = (await session.execute(select(Listing).where(Listing.city == city))).scalars().all()
        for listing in rows:
            await set_manual_archive(session, listing.ad_id, True)
        await session.commit()
    return RedirectResponse(f"{PREFIX}/?status=visible", status_code=303)


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------


@router.get("/cities", response_class=HTMLResponse)
async def cities_page(request: Request, _user=Depends(require_auth)):
    async with get_session() as session:
        cities = (await session.execute(select(CityConfigDB).order_by(CityConfigDB.id))).scalars().all()
    return templates.TemplateResponse(request, "cities.html", {"cities": cities})


@router.post("/cities/add")
async def add_city(
    name: str = Form(...),
    rgn: str = Form(...),
    ar: str = Form(...),
    _user=Depends(require_auth),
):
    async with get_session() as session:
        session.add(CityConfigDB(name=name.strip(), rgn=rgn.strip(), ar=ar.strip(), enabled=True))
        await session.commit()
    return RedirectResponse(f"{PREFIX}/cities", status_code=303)


@router.post("/cities/{city_id}/toggle")
async def toggle_city(city_id: int, _user=Depends(require_auth)):
    async with get_session() as session:
        city = await session.get(CityConfigDB, city_id)
        if city:
            city.enabled = not city.enabled
            await session.commit()
    return RedirectResponse(f"{PREFIX}/cities", status_code=303)


@router.post("/cities/{city_id}/delete")
async def delete_city(city_id: int, _user=Depends(require_auth)):
    async with get_session() as session:
        city = await session.get(CityConfigDB, city_id)
        if city:
            await session.delete(city)
            await session.commit()
    return RedirectResponse(f"{PREFIX}/cities", status_code=303)


# ---------------------------------------------------------------------------
# Task dashboard
# ---------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() if value else None


def _task_run_payload(run: TaskRun) -> dict:
    return {
        "id": run.id,
        "task_name": run.task_name,
        "celery_task_id": run.celery_task_id,
        "status": run.status,
        "mode": run.mode,
        "city_name": run.city_name,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
        "counters": run.counters or {},
        "error": run.error,
    }


def _schedule_payload(schedule: TaskSchedule | None, key="new_listings") -> dict:
    default_interval = (
        config.NEW_LISTINGS_DEFAULT_INTERVAL_SECONDS
        if key == "new_listings"
        else config.FULL_RECONCILIATION_INTERVAL_SECONDS
    )
    return {
        "key": schedule.key if schedule else key,
        "enabled": schedule.enabled if schedule else False,
        "interval_seconds": schedule.interval_seconds if schedule else default_interval,
        "interval_minutes": max(1, (schedule.interval_seconds if schedule else default_interval) // 60),
        "max_pages": (schedule.max_pages if schedule and schedule.max_pages else config.NEW_LISTINGS_MAX_PAGES)
        if key == "new_listings"
        else None,
        "last_enqueued_at": _iso(schedule.last_enqueued_at) if schedule else None,
        "next_run_at": _iso(schedule.next_run_at) if schedule else None,
    }


def _inspect_workers() -> dict:
    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
        queues = inspector.active_queues() or {}
        worker_names = sorted(set(active) | set(reserved) | set(queues))
        workers = []
        for name in worker_names:
            workers.append(
                {
                    "name": name,
                    "active": len(active.get(name, [])),
                    "reserved": len(reserved.get(name, [])),
                    "queues": [q.get("name") for q in queues.get(name, []) if q.get("name")],
                }
            )
        return {"online": len(worker_names), "workers": workers, "error": None}
    except Exception as exc:
        logger.warning("Celery inspect failed: %s", exc)
        return {"online": 0, "workers": [], "error": str(exc)}


async def _tasks_status_payload(run_id: int | None = None) -> dict:
    async with get_session() as session:
        schedule = await session.get(TaskSchedule, "new_listings")
        full_schedule = await session.get(TaskSchedule, "full_reconciliation")
        running = (
            (
                await session.execute(
                    select(TaskRun)
                    .where(TaskRun.status.in_(["queued", "running"]))
                    .order_by(TaskRun.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        recent = (await session.execute(select(TaskRun).order_by(TaskRun.created_at.desc()).limit(30))).scalars().all()

        selected_run_id = run_id
        if selected_run_id is None:
            selected_run_id = running[0].id if running else (recent[0].id if recent else None)

        logs = []
        if selected_run_id is not None:
            logs = (
                (
                    await session.execute(
                        select(TaskLog)
                        .where(TaskLog.run_id == selected_run_id)
                        .order_by(TaskLog.created_at.asc())
                        .limit(300)
                    )
                )
                .scalars()
                .all()
            )

    workers = await asyncio.to_thread(_inspect_workers)
    return {
        "schedule": _schedule_payload(schedule),
        "full_schedule": _schedule_payload(full_schedule, "full_reconciliation"),
        "workers": workers,
        "running": [_task_run_payload(run) for run in running],
        "recent": [_task_run_payload(run) for run in recent],
        "selected_run_id": selected_run_id,
        "logs": [
            {
                "id": log.id,
                "run_id": log.run_id,
                "level": log.level,
                "message": log.message,
                "created_at": _iso(log.created_at),
            }
            for log in logs
        ],
    }


async def _enqueue_scrape_task(task, task_name: str, mode: str, city_name: str | None) -> int:
    normalized_city = city_name.strip() if city_name else None
    normalized_city = normalized_city or None
    run_id = await create_task_run(task_name, mode=mode, city_name=normalized_city)
    async_result = task.apply_async(
        kwargs={"run_id": run_id, "city_name": normalized_city},
        queue="scraper",
    )
    await attach_celery_task_id(run_id, async_result.id)
    return run_id


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, _user=Depends(require_auth)):
    async with get_session() as session:
        cities = (
            (
                await session.execute(
                    select(CityConfigDB).where(CityConfigDB.enabled.is_(True)).order_by(CityConfigDB.name)
                )
            )
            .scalars()
            .all()
        )
    status = await _tasks_status_payload()
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "status": status,
            "cities": cities,
        },
    )


@router.get("/tasks/status")
async def tasks_status(run_id: int | None = None, _user=Depends(require_auth)):
    return JSONResponse(await _tasks_status_payload(run_id))


@router.post("/tasks/run-new-listings")
async def tasks_run_new_listings(city_name: str = Form(default=""), _user=Depends(require_auth)):
    await _enqueue_scrape_task(load_new_listings, "load_new_listings", "incremental", city_name)
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.post("/tasks/run-full-scrape")
async def tasks_run_full_scrape(city_name: str = Form(default=""), _user=Depends(require_auth)):
    await _enqueue_scrape_task(full_scrape, "full_scrape", "full", city_name)
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


async def _save_task_schedule(key, interval_minutes, enabled, max_pages=None):
    interval_seconds = interval_minutes * 60
    now = utc_now()
    async with get_session() as session:
        schedule = await session.get(TaskSchedule, key, with_for_update=True)
        if schedule is None:
            session.add(
                TaskSchedule(
                    key=key,
                    enabled=enabled,
                    interval_seconds=interval_seconds,
                    max_pages=max_pages,
                    next_run_at=now + timedelta(seconds=interval_seconds),
                )
            )
        else:
            reschedule = (
                (not schedule.enabled and enabled)
                or schedule.interval_seconds != interval_seconds
                or as_utc(schedule.next_run_at) is None
                or as_utc(schedule.next_run_at) < now
            )
            schedule.enabled = enabled
            schedule.interval_seconds = interval_seconds
            schedule.max_pages = max_pages
            if reschedule:
                schedule.next_run_at = now + timedelta(seconds=interval_seconds)
        await session.commit()


@router.post("/tasks/schedules/new-listings")
async def tasks_update_new_listings_schedule(
    interval_minutes: int = Form(..., ge=1, le=35791394),
    max_pages: int = Form(..., ge=1, le=2147483647),
    enabled: str | None = Form(default=None),
    _user=Depends(require_auth),
):
    await _save_task_schedule("new_listings", interval_minutes, enabled == "on", max_pages)
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.post("/tasks/schedules/full-reconciliation")
async def tasks_update_full_schedule(
    interval_minutes: int = Form(..., ge=1, le=35791394),
    enabled: str | None = Form(default=None),
    _user=Depends(require_auth),
):
    await _save_task_schedule("full_reconciliation", interval_minutes, enabled == "on")
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.post("/tasks/{run_id}/revoke")
async def tasks_revoke(run_id: int, _user=Depends(require_auth)):
    async with get_session() as session:
        run = await session.get(TaskRun, run_id)
        if run and run.celery_task_id:
            celery_app.control.revoke(run.celery_task_id, terminate=True, signal="SIGTERM")
            run.status = "revoked"
            run.finished_at = utc_now()
            session.add(TaskLog(run_id=run.id, level="warning", message="Task revoked from admin dashboard."))
            await session.commit()
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.get("/parser")
async def parser_redirect(_user=Depends(require_auth)):
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.get("/parser/status")
async def parser_status_redirect(_user=Depends(require_auth)):
    return JSONResponse({"redirect": f"{PREFIX}/tasks", **await _tasks_status_payload()})


@router.post("/parser/run")
async def parser_run_redirect(_user=Depends(require_auth)):
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


@router.post("/parser/cancel")
async def parser_cancel_redirect(_user=Depends(require_auth)):
    return RedirectResponse(f"{PREFIX}/tasks", status_code=303)


app.include_router(router)
