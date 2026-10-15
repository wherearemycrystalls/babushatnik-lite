from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from bot.db.models import BotUser, ListingDelivery, NotificationDelivery, UserFavourite, UserPreference, UserSeen
from bot.services.notification_service import NotificationService
from kufar_scraper.db.client import (
    archive_unseen_from_completed_reconciliation,
    dispose_engine,
    get_session,
    init_engine,
    set_manual_archive,
    upsert_listing,
)
from kufar_scraper.db.models import Listing
from kufar_scraper.models.schemas import ParsedListing

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class _Bot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


@unittest.skipUnless(TEST_DATABASE_URL, "set TEST_DATABASE_URL to run PostgreSQL integration tests")
class PostgresListingPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import patch

        self.wake = patch("background.tasks.process_listing_notifications.apply_async")
        self.wake.start()
        self.addCleanup(self.wake.stop)
        init_engine(TEST_DATABASE_URL)
        async with get_session() as session:
            await session.execute(delete(ListingDelivery))
            await session.execute(delete(NotificationDelivery))
            await session.execute(delete(UserSeen))
            await session.execute(delete(UserFavourite))
            await session.execute(delete(UserPreference))
            await session.execute(delete(BotUser))
            await session.execute(delete(Listing))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await dispose_engine()

    async def test_reconciliation_and_manual_archive_are_independent(self) -> None:
        listing = ParsedListing(ad_id=1001, city="grodno", price_usd=300, rooms="2")
        async with get_session() as session:
            await upsert_listing(session, listing)
            archived = await archive_unseen_from_completed_reconciliation(
                session, "grodno", datetime.now(timezone.utc) + timedelta(hours=1)
            )
            await session.commit()
            self.assertEqual(archived, 1)

            row = await session.get(Listing, 1001)
            self.assertFalse(row.is_source_active)
            self.assertTrue(await set_manual_archive(session, 1001, True))
            await session.commit()
            await upsert_listing(session, listing)
            await session.refresh(row)
            self.assertTrue(row.is_source_active)
            self.assertTrue(row.is_hidden_by_admin)

    async def test_outbox_is_created_then_marked_delivered(self) -> None:
        async with get_session() as session:
            await upsert_listing(
                session,
                ParsedListing(
                    ad_id=1002, city="grodno", price_usd=300, rooms="2", list_time=datetime.now(timezone.utc)
                ),
            )
            now = datetime.now(timezone.utc)
            session.add(
                BotUser(
                    telegram_id=77,
                    notifications=True,
                    notification_interval_minutes=1,
                    notification_cursor_at=now - timedelta(hours=1),
                )
            )
            session.add(UserPreference(telegram_id=77, city="grodno", rooms=["2"], price_usd_max=500))
            await session.commit()

            bot = _Bot()
            service = NotificationService(session, bot)
            scheduled = await service.schedule_due_deliveries(now)
            delivered = await service.deliver_pending(now)
            row = (await session.execute(select(ListingDelivery))).scalar_one()

            self.assertEqual(scheduled["deliveries_created"], 1)
            self.assertEqual(delivered["cards_sent"], 1)
            self.assertEqual(row.status, "delivered")
            self.assertEqual(len(bot.messages), 1)

    async def test_delayed_search_snapshot_cannot_reactivate_a_later_archive(self):
        from kufar_scraper.db.models import ListingChange

        now = datetime.now(timezone.utc)
        listing = ParsedListing(ad_id=1001, city="grodno", source_observed_at=now)
        async with get_session() as session:
            await upsert_listing(session, listing)
            await archive_unseen_from_completed_reconciliation(session, "grodno", now + timedelta(seconds=1))
            await session.commit()
            await upsert_listing(session, listing)
            row = await session.get(Listing, 1001)
            self.assertFalse(row.is_source_active)
            await upsert_listing(session, listing.model_copy(update={"source_observed_at": datetime.now(timezone.utc)}))
            await session.refresh(row)
            self.assertTrue(row.is_source_active)
            kinds = list((await session.execute(select(ListingChange.kind).order_by(ListingChange.id))).scalars())
            self.assertEqual(kinds, ["discovered", "source_inactive", "updated"])

    async def test_source_changes_preserve_detection_time_and_do_not_notify_again(self):
        from copy import deepcopy

        from sqlalchemy import func, update
        from test_source_metadata import payload

        from kufar_scraper.db.models import ListingChange
        from kufar_scraper.scraper.parser import parse_ad

        now = datetime.now(timezone.utc)
        raw = payload()
        for data in [raw, raw["_detail_response"]["result"]]:
            data["list_time"] = (now - timedelta(minutes=30)).isoformat()
        raw["_observed_at"] = now.isoformat()
        raw["_detail_fetched_at"] = now.isoformat()
        async with get_session() as session:
            await upsert_listing(session, parse_ad(raw))
            await session.execute(update(Listing).values(created_at=now - timedelta(hours=2)))
            await session.commit()
            initial = await session.get(Listing, 1001)
            first_date = initial.first_list_time
            created = initial.created_at
            newer = deepcopy(raw)
            for data in [newer, newer["_detail_response"]["result"]]:
                data["list_time"] = (now - timedelta(seconds=1)).isoformat()
                data["price_usd"] = "35050"
            newer["_observed_at"] = (now + timedelta(seconds=1)).isoformat()
            newer["_detail_fetched_at"] = newer["_observed_at"]
            self.assertEqual(await upsert_listing(session, parse_ad(newer)), "updated")
            await session.refresh(initial)
            self.assertEqual(initial.created_at, created)
            self.assertEqual(initial.first_list_time, first_date)
            self.assertEqual(str(initial.price_usd), "350.50")
            self.assertGreater(initial.list_time, first_date)
            changes = list(
                (await session.execute(select(ListingChange).where(ListingChange.kind == "updated"))).scalars()
            )
            self.assertEqual(len(changes), 1)
            self.assertIn("list_time", changes[0].changes)
            self.assertIn("price_usd", changes[0].changes)
            self.assertEqual(await upsert_listing(session, parse_ad(newer)), "unchanged")
            count = await session.scalar(select(func.count()).select_from(ListingChange))
            self.assertEqual(count, 2)
            prefs = UserPreference(telegram_id=999, city="grodno")
            service = NotificationService(session, _Bot())
            self.assertEqual(
                await service._new_by_criteria_count(
                    999, prefs, now - timedelta(minutes=5), now + timedelta(minutes=1), now - timedelta(hours=3)
                ),
                0,
            )

    async def test_delayed_recent_source_is_new_but_historical_import_is_not(self):
        now = datetime.now(timezone.utc)
        async with get_session() as session:
            await upsert_listing(session, ParsedListing(ad_id=1, city="grodno", list_time=now - timedelta(days=7)))
            await upsert_listing(session, ParsedListing(ad_id=2, city="grodno", list_time=now - timedelta(minutes=30)))
            await upsert_listing(session, ParsedListing(ad_id=3, city="grodno"))
            until = datetime.now(timezone.utc)
            prefs = UserPreference(telegram_id=999, city="grodno")
            count = await NotificationService(session, _Bot())._new_by_criteria_count(
                999, prefs, now - timedelta(minutes=5), until, now - timedelta(hours=1)
            )
            self.assertEqual(count, 1)

    async def test_partial_search_refresh_preserves_full_description_and_detail_only_fields(self):
        from copy import deepcopy

        from sqlalchemy import func
        from test_source_metadata import payload

        from kufar_scraper.db.models import ListingChange
        from kufar_scraper.scraper.parser import parse_ad

        raw = payload()
        async with get_session() as session:
            await upsert_listing(session, parse_ad(raw))
            partial = deepcopy(raw)
            partial.pop("_detail_response")
            partial.pop("_detail_fetched_at")
            partial["_observed_at"] = "2026-10-15T12:00:02Z"
            self.assertEqual(await upsert_listing(session, parse_ad(partial)), "unchanged")
            row = await session.get(Listing, 1001)
            self.assertIn("Вторая строка", row.description)
            self.assertEqual(row.parameters["condition"], "1")
            self.assertEqual(await session.scalar(select(func.count()).select_from(ListingChange)), 1)
            partial["price_usd"] = "36075"
            partial["_detail_failed"] = True
            self.assertEqual(await upsert_listing(session, parse_ad(partial)), "updated")
            await session.refresh(row)
            self.assertEqual(str(row.price_usd), "360.75")
            self.assertIn("Вторая строка", row.description)
            self.assertTrue(row.raw_detail)

    async def test_older_observation_cannot_overwrite_newer_metadata(self):
        from copy import deepcopy

        from test_source_metadata import payload

        from kufar_scraper.scraper.parser import parse_ad

        raw = payload()
        async with get_session() as session:
            await upsert_listing(session, parse_ad(raw))
            stale = deepcopy(raw)
            stale["_observed_at"] = "2026-10-15T11:59:00Z"
            stale["_detail_fetched_at"] = stale["_observed_at"]
            stale["_detail_response"]["result"]["subject"] = "Stale title"
            self.assertEqual(await upsert_listing(session, parse_ad(stale)), "unchanged")
            self.assertEqual((await session.get(Listing, 1001)).title, "Пример квартиры")

    async def test_masked_search_account_id_preserves_richer_detail_id(self):
        from test_source_metadata import payload

        from kufar_scraper.scraper.parser import parse_ad

        raw = payload()
        raw["account_id"] = 0
        async with get_session() as session:
            await upsert_listing(session, parse_ad(raw))
            expected = raw["_detail_response"]["result"]["account_id"]
            raw.pop("_detail_response")
            raw.pop("_detail_fetched_at")
            raw["_observed_at"] = "2026-10-15T12:00:02Z"
            self.assertEqual(await upsert_listing(session, parse_ad(raw)), "unchanged")
            self.assertEqual((await session.get(Listing, 1001)).account_id, expected)

    async def test_feed_orders_by_source_time_not_ingestion_updates(self):
        from sqlalchemy import update

        now = datetime.now(timezone.utc)
        async with get_session() as session:
            await upsert_listing(session, ParsedListing(ad_id=1, city="grodno", list_time=now - timedelta(days=1)))
            await upsert_listing(session, ParsedListing(ad_id=2, city="grodno", list_time=now))
            await upsert_listing(session, ParsedListing(ad_id=3, city="grodno"))
            await session.execute(update(Listing).where(Listing.ad_id == 1).values(updated_at=now + timedelta(days=1)))
            session.add(
                BotUser(
                    telegram_id=999,
                    notifications=True,
                    notification_cursor_at=now - timedelta(hours=1),
                    notification_started_at=now - timedelta(days=2),
                )
            )
            session.add(UserPreference(telegram_id=999, city="grodno"))
            await session.commit()
            await NotificationService(session, _Bot()).schedule_due_deliveries(datetime.now(timezone.utc))
            ids = list((await session.scalars(select(ListingDelivery.ad_id).order_by(ListingDelivery.id))).all())
            self.assertEqual(ids, [1, 2])

    async def test_notification_scan_waits_for_an_inflight_ingestion_transaction(self):
        import asyncio

        now = datetime.now(timezone.utc)
        async with get_session() as writer, get_session() as reader:
            await upsert_listing(writer, ParsedListing(ad_id=1, city="grodno", list_time=now), commit=False)
            until = datetime.now(timezone.utc)
            prefs = UserPreference(telegram_id=999, city="grodno")
            task = asyncio.create_task(
                NotificationService(reader, _Bot())._new_by_criteria_count(
                    999, prefs, now - timedelta(hours=1), until, now - timedelta(hours=1)
                )
            )
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.05)
            await writer.commit()
            self.assertEqual(await asyncio.wait_for(task, 5), 1)

    async def test_independent_full_refresh_collects_all_429_listings(self):
        from unittest.mock import patch

        from test_incremental_fetch import FakeKufarClient, _ad, _page

        from background.task_db import create_task_run
        from background.tasks import _full_reconciliation
        from kufar_scraper.db.models import CityConfigDB

        now = datetime.now(timezone.utc)
        async with get_session() as session:
            city = (await session.execute(select(CityConfigDB).where(CityConfigDB.name == "grodno"))).scalar_one()
            city.catalog_initialized_at = None
            await session.commit()
        pages = [
            _page(
                [_ad(10000 + i, now.isoformat()) for i in range(start, min(start + 30, 429))],
                f"n{start}" if start + 30 < 429 else None,
            )
            for start in range(0, 429, 30)
        ]
        run_id = await create_task_run("load_new_listings", city_name="grodno")
        with (
            patch("background.tasks.KufarClient", return_value=FakeKufarClient(pages)),
            patch("kufar_scraper.scraper.client.PAGE_DELAY", 0),
        ):
            result = await _full_reconciliation(run_id, "grodno")
        self.assertEqual(result["inserted"], 429)
        self.assertEqual(result["full_fallbacks"], 0)
        async with get_session() as session:
            self.assertIsNotNone(
                await session.scalar(select(CityConfigDB.catalog_initialized_at).where(CityConfigDB.name == "grodno"))
            )

    async def test_incremental_page_limit_from_database_is_strict_even_on_first_run(self):
        from unittest.mock import patch

        from test_incremental_fetch import FakeKufarClient, _ad, _page

        from background.task_db import create_task_run
        from background.tasks import _incremental_scrape
        from kufar_scraper.db.models import CityConfigDB, TaskSchedule

        async with get_session() as session:
            schedule = await session.get(TaskSchedule, "new_listings")
            schedule.max_pages = 1
            city = await session.scalar(select(CityConfigDB).where(CityConfigDB.name == "grodno"))
            city.catalog_initialized_at = None
            await session.commit()
        client = FakeKufarClient([_page([_ad(1)], "a"), _page([_ad(2)])])
        run_id = await create_task_run("load_new_listings", city_name="grodno")
        with (
            patch("background.tasks.KufarClient", return_value=client),
            patch("kufar_scraper.scraper.client.PAGE_DELAY", 0),
        ):
            result = await _incremental_scrape(run_id, "grodno")
        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["page_limit_reached"], 1)
        self.assertEqual(result["full_fallbacks"], 0)
        self.assertEqual(len(client.pages), 1)

    async def test_failed_full_scan_saves_partial_data_without_archiving(self):
        from unittest.mock import patch

        from test_incremental_fetch import FakeKufarClient, _ad, _page

        from background.task_db import create_task_run
        from background.tasks import ScrapeFailure, _full_reconciliation

        async with get_session() as session:
            await upsert_listing(session, ParsedListing(ad_id=1, city="grodno"))
        run_id = await create_task_run("full_scrape", city_name="grodno")
        with (
            patch(
                "background.tasks.KufarClient",
                return_value=FakeKufarClient([_page([_ad(2)], "a"), RuntimeError("offline")]),
            ),
            patch("kufar_scraper.scraper.client.PAGE_DELAY", 0),
        ):
            with self.assertRaises(ScrapeFailure) as error:
                await _full_reconciliation(run_id, "grodno")
        self.assertEqual(error.exception.counters["inserted"], 1)
        async with get_session() as session:
            self.assertTrue((await session.get(Listing, 1)).is_source_active)
            self.assertTrue((await session.get(Listing, 2)).is_source_active)
