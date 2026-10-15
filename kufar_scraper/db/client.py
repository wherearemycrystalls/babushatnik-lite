import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kufar_scraper.db.models import CityConfigDB, Listing, ListingChange, TaskSchedule
from kufar_scraper.models.schemas import CityConfig, ParsedListing
from kufar_scraper.scraper.parser import PARAMETER_COLUMNS


def _make_engine(database_url: str):
    return create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=3,
        max_overflow=5,
    )


_engine = None
_session_factory = None
_database_url = None


def init_engine(database_url: str) -> None:
    global _engine, _session_factory, _database_url
    if _engine is not None and _database_url == database_url:
        return
    _engine = _make_engine(database_url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    _database_url = database_url


async def dispose_engine() -> None:
    """Release pooled connections; used by isolated integration tests and shutdown tooling."""
    global _engine, _session_factory, _database_url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    _database_url = None


@asynccontextmanager
async def get_session():
    async with _session_factory() as session:
        yield session


async def get_active_cities(session: AsyncSession) -> list[CityConfig]:
    result = await session.execute(select(CityConfigDB).where(CityConfigDB.enabled.is_(True)))
    return [CityConfig(name=row.name, rgn=row.rgn, ar=row.ar) for row in result.scalars().all()]


async def seed_default_cities(session: AsyncSession, defaults: list[CityConfig]) -> None:
    """Create configured cities once without overwriting admin changes."""
    existing = set((await session.execute(select(CityConfigDB.name))).scalars().all())
    for city in defaults:
        if city.name not in existing:
            session.add(CityConfigDB(name=city.name, rgn=city.rgn, ar=city.ar))
    await session.commit()


async def seed_default_task_schedules(
    session: AsyncSession,
    *,
    new_listings_interval_seconds: int,
    full_reconciliation_interval_seconds: int,
    new_listings_max_pages: int = 5,
) -> None:
    """Create scheduler records once; later admin edits remain authoritative."""
    now = datetime.now(timezone.utc)
    defaults = {
        "new_listings": new_listings_interval_seconds,
        "full_reconciliation": full_reconciliation_interval_seconds,
    }
    for key, interval_seconds in defaults.items():
        if await session.get(TaskSchedule, key) is None:
            session.add(
                TaskSchedule(
                    key=key,
                    enabled=True,
                    interval_seconds=interval_seconds,
                    max_pages=new_listings_max_pages if key == "new_listings" else None,
                    next_run_at=now if key == "full_reconciliation" else now + timedelta(seconds=interval_seconds),
                )
            )
    await session.commit()


async def archive_unseen_from_completed_reconciliation(
    session: AsyncSession,
    city_name: str,
    started_at: datetime,
) -> int:
    """Archive only a completed catalog, retaining observed availability history."""
    await session.execute(select(func.pg_advisory_xact_lock(CATALOG_LOCK)))
    observed = datetime.now(timezone.utc)
    result = await session.execute(
        update(Listing)
        .where(
            Listing.city == city_name,
            Listing.is_source_active.is_(True),
            (Listing.last_seen_at.is_(None)) | (Listing.last_seen_at < started_at),
        )
        .values(is_source_active=False, source_inactive_at=observed, content_changed_at=observed)
        .returning(Listing.ad_id)
    )
    ids = list(result.scalars())
    session.add_all(
        [
            ListingChange(
                ad_id=ad_id,
                observed_at=observed,
                kind="source_inactive",
                changes={"is_source_active": {"old": True, "new": False}},
            )
            for ad_id in ids
        ]
    )
    return len(ids)


async def set_manual_archive(session: AsyncSession, ad_id: int, archived: bool) -> bool:
    listing = await session.get(Listing, ad_id)
    if listing is None:
        return False
    if listing.is_hidden_by_admin == archived:
        return True
    observed = datetime.now(timezone.utc)
    session.add(
        ListingChange(
            ad_id=ad_id,
            observed_at=observed,
            kind="manual_hidden" if archived else "manual_restored",
            changes={"is_hidden_by_admin": {"old": listing.is_hidden_by_admin, "new": archived}},
        )
    )
    listing.is_hidden_by_admin = archived
    listing.manual_archived_at = observed if archived else None
    await session.flush()
    return True


# Exclusive ingestion and shared notification scans fence commit-time discovery.
# A transaction opened before a scan cannot commit unseen rows behind its cursor.
CATALOG_LOCK = 540649909897
OBSERVATION_FIELDS = {"raw_search", "raw_detail", "source_observed_at", "details_fetched_at", "data_warnings"}
CONTENT_FIELDS = set(ParsedListing.model_fields) - OBSERVATION_FIELDS - {"ad_id"}


async def lock_catalog_for_notifications(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock_shared(CATALOG_LOCK)))


def _json_value(value):
    return json.loads(
        json.dumps(
            value,
            default=lambda item: (
                item.isoformat()
                if isinstance(item, datetime)
                else format(item.normalize(), "f")
                if isinstance(item, Decimal)
                else str(item)
            ),
        )
    )


async def upsert_listing(
    session: AsyncSession,
    listing: ParsedListing,
    *,
    commit: bool = True,
) -> str:
    """Store source snapshots, preserve richer details on partial refresh and audit actual changes."""
    await session.execute(select(func.pg_advisory_xact_lock(CATALOG_LOCK)))
    data = listing.model_dump()
    observed_at = listing.source_observed_at or datetime.now(timezone.utc)
    version_at = max(observed_at, listing.details_fetched_at or observed_at)
    data["source_observed_at"] = version_at
    stmt = (
        insert(Listing)
        .values(
            **data,
            first_list_time=listing.list_time,
            last_seen_at=observed_at,
            content_changed_at=version_at,
            is_source_active=True,
            created_at=func.clock_timestamp(),
        )
        .on_conflict_do_nothing(index_elements=["ad_id"])
        .returning(Listing.ad_id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        session.add(
            ListingChange(
                ad_id=listing.ad_id,
                observed_at=version_at,
                kind="discovered",
                changes={
                    key: {"old": None, "new": _json_value(data[key])}
                    for key in CONTENT_FIELDS
                    if data.get(key) is not None
                },
            )
        )
        if commit:
            await session.commit()
        return "inserted"

    record = (
        await session.execute(select(Listing).where(Listing.ad_id == listing.ad_id).with_for_update())
    ).scalar_one()
    if record.source_observed_at and version_at < record.source_observed_at:
        if commit:
            await session.commit()
        return "unchanged"

    if listing.raw_detail is None:
        # A short search result or failed detail request must not erase full data.
        data["raw_detail"] = record.raw_detail
        data["details_fetched_at"] = record.details_fetched_at
        if not data["account_id"]:
            # Kufar masks account_id as zero in search; the detail response has the actual ID.
            detail = (record.raw_detail or {}).get("result", record.raw_detail or {})
            account_id = detail.get("account_id")
            data["account_id"] = record.account_id or (
                int(account_id) if str(account_id).isdigit() and int(account_id) > 0 else None
            )
        if record.details_fetched_at:
            data["description"] = record.description
        data["parameters"] = {**(record.parameters or {}), **listing.parameters}
        data["parameter_details"] = {}
        for scope in ["ad", "account"]:
            previous = (record.parameter_details or {}).get(scope, {})
            merged = dict(previous)
            for key, entry in listing.parameter_details.get(scope, {}).items():
                old = previous.get(key, {})
                # Search/detail use different labels for the same value. Retain canonical detail labels.
                merged[key] = {**entry, **old} if old and old.get("v") == entry.get("v") else {**old, **entry}
            data["parameter_details"][scope] = merged
        for field, parameter in PARAMETER_COLUMNS.items():
            if record.raw_detail and (
                parameter not in listing.parameters
                or (
                    field in {"repair", "house_type", "bathroom", "balcony", "prepayment"}
                    and listing.parameters.get(parameter) == (record.parameters or {}).get(parameter)
                )
            ):
                data[field] = getattr(record, field)
        if record.list_time and listing.list_time and listing.list_time < record.list_time:
            data["list_time"] = record.list_time
        if listing.list_time is None:
            data["list_time"] = record.list_time
        if not listing.raw_search:
            for field in CONTENT_FIELDS - listing.model_fields_set:
                data[field] = getattr(record, field)

    changes = {}
    for key in CONTENT_FIELDS:
        old, new = _json_value(getattr(record, key)), _json_value(data[key])
        if old != new:
            changes[key] = {"old": old, "new": new}
    reactivate = record.source_inactive_at is None or observed_at >= record.source_inactive_at
    if not record.is_source_active and reactivate:
        changes["is_source_active"] = {"old": False, "new": True}
    for key, value in data.items():
        if key != "ad_id":
            setattr(record, key, value)
    if record.first_list_time is None and record.list_time is not None:
        record.first_list_time = record.list_time
    record.last_seen_at = max(record.last_seen_at or observed_at, observed_at)
    if reactivate:
        record.is_source_active = True
        record.source_inactive_at = None
    record.updated_at = datetime.now(timezone.utc)
    if changes:
        record.content_changed_at = version_at
        session.add(ListingChange(ad_id=record.ad_id, observed_at=version_at, kind="updated", changes=changes))
    if commit:
        await session.commit()
    return "updated" if changes else "unchanged"
