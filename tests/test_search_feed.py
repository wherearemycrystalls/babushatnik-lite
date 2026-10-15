import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import delete, func, select, update

from bot.db.models import BotUser, ListingDelivery, NotificationDelivery, UserFavourite, UserPreference, UserSeen
from bot.services.availability_service import AvailabilityService
from bot.services.listing_service import ListingService
from bot.services.notification_service import NotificationService
from bot.services.user_service import UserService
from kufar_scraper.db.client import dispose_engine, get_session, init_engine, upsert_listing
from kufar_scraper.db.models import CityConfigDB, Listing
from kufar_scraper.models.schemas import ParsedListing
from kufar_scraper.scraper.client import FullFetchResult

URL = os.getenv("TEST_DATABASE_URL")


class FakeBot:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    async def send_message(self, **kwargs):
        if self.error:
            raise self.error
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))


@unittest.skipUnless(URL, "set TEST_DATABASE_URL for PostgreSQL tests")
class SearchFeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_engine(URL)
        self.now = datetime.now(timezone.utc)
        async with get_session() as session:
            for model in [
                ListingDelivery,
                NotificationDelivery,
                UserSeen,
                UserFavourite,
                UserPreference,
                BotUser,
                Listing,
            ]:
                await session.execute(delete(model))
            city = await session.scalar(select(CityConfigDB).where(CityConfigDB.name == "grodno"))
            city.availability_checked_at = None
            session.add(
                BotUser(
                    telegram_id=1,
                    notifications=True,
                    notification_cursor_at=self.now - timedelta(seconds=1),
                    notification_started_at=self.now - timedelta(hours=1),
                    notification_interval_minutes=1440,
                )
            )
            session.add(UserPreference(telegram_id=1, city="grodno", rooms=["2"], price_usd_max=500))
            await session.commit()
        self.sleep = patch("bot.services.notification_service.asyncio.sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def asyncTearDown(self):
        await dispose_engine()

    async def listing(self, session, ad_id=1, **overrides):
        values = {"ad_id": ad_id, "city": "grodno", "rooms": "2", "price_usd": 300, "list_time": self.now, **overrides}
        await upsert_listing(session, ParsedListing(**values))

    async def schedule(self, session):
        return await NotificationService(session, FakeBot()).schedule_due_deliveries(datetime.now(timezone.utc))

    async def test_each_new_match_gets_its_own_card_without_interval_wait(self):
        async with get_session() as session:
            await self.listing(session, 1)
            await self.listing(session, 2)
            await self.listing(session, 3, price_usd=600)
            await self.listing(session, 4, list_time=self.now - timedelta(days=7))
            await self.listing(session, 5, list_time=None)
            bot = FakeBot()
            service = NotificationService(session, bot)
            scheduled = await service.schedule_due_deliveries(datetime.now(timezone.utc))
            result = await service.deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(scheduled["deliveries_created"], 2)
            self.assertEqual(result["cards_sent"], 2)
            self.assertEqual(len(bot.messages), 2)
            self.assertTrue(all("reply_markup" in message for message in bot.messages))
            self.assertFalse(any("новых квартир" in message["text"] for message in bot.messages))

    async def test_repeated_schedule_and_delivery_do_not_resend_an_id(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            user = await session.get(BotUser, 1)
            user.notification_cursor_at = self.now - timedelta(minutes=1)
            await session.commit()
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)
            bot = FakeBot()
            service = NotificationService(session, bot)
            await service.deliver_pending(datetime.now(timezone.utc))
            await service.deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(len(bot.messages), 1)

    async def test_known_id_source_time_change_does_not_create_a_card(self):
        async with get_session() as session:
            await self.listing(session, list_time=self.now - timedelta(days=1))
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)

    async def test_transient_failure_retries_only_the_failed_card(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            failure = FakeBot(RuntimeError("temporary outage"))
            result = await NotificationService(session, failure).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(result["retry_scheduled"], 1)
            delivery = await session.scalar(select(ListingDelivery))
            self.assertEqual(delivery.status, "pending")
            self.assertGreater(delivery.next_attempt_at, datetime.now(timezone.utc))
            bot = FakeBot()
            delivery.next_attempt_at = self.now - timedelta(seconds=1)
            await session.commit()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(len(bot.messages), 1)
            self.assertEqual(delivery.attempts, 2)

    async def test_source_removal_before_send_cancels_the_card(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            await session.execute(update(Listing).values(is_source_active=False))
            await session.commit()
            bot = FakeBot()
            self.assertEqual(
                (await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc)))["cancelled"], 1
            )
            self.assertEqual(bot.messages, [])

    async def test_pause_cancels_pending_cards_and_preserves_favourites(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            await ListingService(session).save_favourite(1, 1)
            await UserService(session).set_notifications(1, False)
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(bot.messages, [])
            self.assertEqual(await ListingService(session).favourite_counts(1), (1, 0))
            await UserService(session).set_notifications(1, True)
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)

    async def test_pause_button_is_idempotent(self):
        async with get_session() as session:
            self.assertFalse(await UserService(session).set_notifications(1, False))
            self.assertFalse(await UserService(session).set_notifications(1, False))

    async def test_criteria_change_cancels_old_pending_matches_without_clearing_favourites(self):
        async with get_session() as session:
            await self.listing(session)
            await ListingService(session).save_favourite(1, 1)
            await self.schedule(session)
            await UserService(session).save_preferences(1, city="grodno", rooms=["1"])
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(bot.messages, [])
            self.assertEqual(await ListingService(session).favourite_counts(1), (1, 0))

    async def test_reset_preferences_cancels_waiting_cards(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            await UserService(session).reset_preferences(1)
            self.assertIsNone(await UserService(session).get_preferences(1))
            self.assertEqual((await session.scalar(select(ListingDelivery))).status, "cancelled")

    async def test_forbidden_user_is_disabled_and_remaining_cards_cancelled(self):
        async with get_session() as session:
            await self.listing(session, 1)
            await self.listing(session, 2)
            await self.schedule(session)
            bot = FakeBot(TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="blocked"))
            self.assertEqual(
                (await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc)))["disabled_users"],
                1,
            )
            self.assertFalse((await session.get(BotUser, 1)).is_active)
            self.assertEqual(set((await session.scalars(select(ListingDelivery.status))).all()), {"cancelled"})

    async def test_expired_lease_is_recovered(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            await session.execute(
                update(ListingDelivery).values(status="processing", lease_expires_at=self.now - timedelta(minutes=1))
            )
            await session.commit()
            bot = FakeBot()
            self.assertEqual(
                (await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc)))["cards_sent"], 1
            )

    async def test_concurrent_scans_create_one_delivery(self):
        async with get_session() as session:
            await self.listing(session)
        async with get_session() as first, get_session() as second:
            await asyncio.gather(self.schedule(first), self.schedule(second))
        async with get_session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(ListingDelivery)), 1)

    async def test_favourite_archives_and_returns_when_source_status_changes(self):
        async with get_session() as session:
            await self.listing(session)
            service = ListingService(session)
            await service.save_favourite(1, 1)
            await service.save_favourite(1, 1)
            self.assertEqual(await service.favourite_counts(1), (1, 0))
            await session.execute(update(Listing).values(is_source_active=False))
            await session.commit()
            self.assertEqual(await service.favourite_counts(1), (0, 1))
            await session.execute(update(Listing).values(is_source_active=True))
            await session.commit()
            self.assertEqual(await service.favourite_counts(1), (1, 0))

    async def test_clear_archive_and_active_are_scoped_by_user_and_current_status(self):
        async with get_session() as session:
            session.add(BotUser(telegram_id=2))
            await session.commit()
            for ad_id in [1, 2]:
                await self.listing(session, ad_id)
                await ListingService(session).save_favourite(1, ad_id)
                await ListingService(session).save_favourite(2, ad_id)
            await session.execute(update(Listing).where(Listing.ad_id == 2).values(is_source_active=False))
            await session.commit()
            service = ListingService(session)
            self.assertEqual(await service.clear_favourites(1, archived=True), 1)
            self.assertEqual(await service.favourite_counts(1), (1, 0))
            self.assertEqual(await service.favourite_counts(2), (1, 1))
            self.assertEqual(await service.clear_favourites(1, archived=False), 1)
            self.assertEqual(await service.favourite_counts(2), (1, 1))

    async def test_favourites_pagination_exposes_more_than_ten_apartments(self):
        async with get_session() as session:
            service = ListingService(session)
            for ad_id in range(1, 24):
                await self.listing(session, ad_id)
                await service.save_favourite(1, ad_id)
            rows, count, page = await service.favourite_page(1, page=2)
            self.assertEqual((len(rows), count, page), (3, 23, 2))

    async def test_successful_availability_check_archives_only_absent_records(self):
        async with get_session() as session:
            await self.listing(session, 1)
            await self.listing(session, 2)
            await ListingService(session).save_favourite(1, 2)
        raw = {
            "ad_id": 1,
            "_city": "grodno",
            "_observed_at": datetime.now(timezone.utc).isoformat(),
            "list_time": self.now.isoformat(),
        }
        client = AsyncMock()
        client.__aenter__.return_value = client

        async def current_snapshot(*args, **kwargs):
            raw["_observed_at"] = datetime.now(timezone.utc).isoformat()
            return FullFetchResult(ads=[raw], completed=True)

        client.fetch_all_ads.side_effect = current_snapshot
        with patch("bot.services.availability_service.KufarClient", return_value=client):
            self.assertEqual(await AvailabilityService(get_session).check_city("grodno"), "checked")
            self.assertEqual(await AvailabilityService(get_session).check_city("grodno"), "cached")
        self.assertFalse(client.fetch_all_ads.call_args.kwargs["fetch_details"])
        async with get_session() as session:
            self.assertTrue((await session.get(Listing, 1)).is_source_active)
            self.assertFalse((await session.get(Listing, 2)).is_source_active)
            self.assertEqual(await ListingService(session).favourite_counts(1), (0, 1))

    async def test_failed_availability_check_never_archives_favourites(self):
        async with get_session() as session:
            await self.listing(session)
            await ListingService(session).save_favourite(1, 1)
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.fetch_all_ads.return_value = FullFetchResult(ads=[], completed=False, error="offline")
        with patch("bot.services.availability_service.KufarClient", return_value=client):
            self.assertEqual(await AvailabilityService(get_session).check_city("grodno"), "unavailable")
        async with get_session() as session:
            self.assertEqual(await ListingService(session).favourite_counts(1), (1, 0))

    async def test_rate_limit_defers_other_cards_for_the_same_user(self):
        async with get_session() as session:
            await self.listing(session, 1)
            await self.listing(session, 2)
            await self.schedule(session)
            bot = FakeBot(TelegramRetryAfter(method=SendMessage(chat_id=1, text="x"), message="slow", retry_after=45))
            result = await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(result["retry_scheduled"], 1)
            rows = list((await session.scalars(select(ListingDelivery))).all())
            self.assertTrue(
                all(row.next_attempt_at > datetime.now(timezone.utc) + timedelta(seconds=40) for row in rows)
            )

    async def test_saving_new_criteria_resumes_a_paused_search(self):
        async with get_session() as session:
            await UserService(session).set_notifications(1, False)
            await UserService(session).save_preferences(1, city="grodno", rooms=["2"])
            self.assertTrue((await session.get(BotUser, 1)).notifications)

    async def test_saving_criteria_cannot_pull_in_a_preexisting_catalog_row(self):
        async with get_session() as session:
            await self.listing(session)
            await UserService(session).save_preferences(1, city="grodno", rooms=["2"])
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)

    async def test_new_ingestion_wakes_the_feed_only_after_successful_save(self):
        from background.task_db import create_task_run
        from background.tasks import _save_raw_ads

        run_id = await create_task_run("load_new_listings", city_name="grodno")
        raw = {"ad_id": 99, "_city": "grodno", "list_time": self.now.isoformat()}
        with patch("background.tasks.process_listing_notifications.apply_async") as wake:
            self.assertEqual((await _save_raw_ads([raw], run_id))["inserted"], 1)
            wake.assert_called_once_with(queue="bot")
            wake.reset_mock()
            await _save_raw_ads([raw], run_id)
            wake.assert_not_called()

    async def test_concurrent_workers_do_not_claim_the_same_card(self):
        async with get_session() as session:
            await self.listing(session, 1)
            await self.listing(session, 2)
            await self.schedule(session)
        bot = FakeBot()
        async with get_session() as one, get_session() as two:
            await asyncio.gather(
                NotificationService(one, bot).deliver_pending(datetime.now(timezone.utc)),
                NotificationService(two, bot).deliver_pending(datetime.now(timezone.utc)),
            )
        self.assertEqual(len(bot.messages), 2)

    async def test_price_change_before_delivery_is_rechecked(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            await session.execute(update(Listing).values(price_usd=900))
            await session.commit()
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(bot.messages, [])

    async def add_profile(self, session, name="Родителям", **fields):
        profile = await UserService(session).save_profile(
            1, name=name, city="grodno", rooms=["2"], price_usd_max=fields.get("price_usd_max", 400)
        )
        return await UserService(session).set_profile_enabled(1, profile.id, True)

    async def test_overlapping_profiles_send_one_card_with_both_names(self):
        async with get_session() as session:
            await self.add_profile(session)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            self.assertEqual((await self.schedule(session))["deliveries_created"], 1)
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(len(bot.messages), 1)
            self.assertIn("Основной", bot.messages[0]["text"])
            self.assertIn("Родителям", bot.messages[0]["text"])
            delivery = await session.scalar(select(ListingDelivery))
            self.assertEqual([p["name"] for p in delivery.matched_profiles], ["Основной", "Родителям"])

    async def test_only_matching_enabled_profiles_are_mentioned(self):
        async with get_session() as session:
            other = await self.add_profile(session, price_usd_max=200)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            await self.schedule(session)
            delivery = await session.scalar(select(ListingDelivery))
            self.assertEqual([p["name"] for p in delivery.matched_profiles], ["Основной"])
            await UserService(session).set_profile_enabled(1, other.id, False)
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertNotIn("Родителям", bot.messages[0]["text"])

    async def test_disabling_one_profile_revalidates_pending_card_against_the_other(self):
        async with get_session() as session:
            other = await self.add_profile(session)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            await self.schedule(session)
            await UserService(session).set_profile_enabled(1, other.id, False)
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(len(bot.messages), 1)
            self.assertIn("Основной", bot.messages[0]["text"])
            self.assertNotIn("Родителям", bot.messages[0]["text"])

    async def test_disabling_all_profiles_cancels_a_pending_card(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            profile = (await UserService(session).get_profiles(1))[0]
            await UserService(session).set_profile_enabled(1, profile.id, False)
            bot = FakeBot()
            result = await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(result["cancelled"], 1)
            self.assertEqual(bot.messages, [])

    async def test_reenabled_profile_starts_with_new_listings_only(self):
        async with get_session() as session:
            service = UserService(session)
            profile = (await service.get_profiles(1))[0]
            await service.set_profile_enabled(1, profile.id, False)
            await self.listing(session)
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)
            await service.set_profile_enabled(1, profile.id, True)
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)
            await self.listing(session, 2, list_time=datetime.now(timezone.utc))
            self.assertEqual((await self.schedule(session))["deliveries_created"], 1)

    async def test_adding_a_profile_does_not_advance_other_profiles_cursor_or_cancel_their_cards(self):
        async with get_session() as session:
            await self.listing(session)
            await self.schedule(session)
            before = (await session.get(BotUser, 1)).notification_cursor_at
            await self.add_profile(session)
            self.assertEqual((await session.get(BotUser, 1)).notification_cursor_at, before)
            bot = FakeBot()
            await NotificationService(session, bot).deliver_pending(datetime.now(timezone.utc))
            self.assertEqual(len(bot.messages), 1)
            self.assertNotIn("Родителям", bot.messages[0]["text"])

    async def test_editing_one_profile_does_not_lose_a_new_match_for_another(self):
        async with get_session() as session:
            other = await self.add_profile(session)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            before = (await session.get(BotUser, 1)).notification_cursor_at
            await UserService(session).save_profile(
                1, profile_id=other.id, name="Новый набор", city="grodno", rooms=["1"]
            )
            self.assertEqual((await session.get(BotUser, 1)).notification_cursor_at, before)
            self.assertEqual((await self.schedule(session))["deliveries_created"], 1)
            delivery = await session.scalar(select(ListingDelivery))
            self.assertEqual([p["name"] for p in delivery.matched_profiles], ["Основной"])

    async def test_profile_name_snapshot_survives_profile_deletion(self):
        async with get_session() as session:
            other = await self.add_profile(session)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            await self.schedule(session)
            await NotificationService(session, FakeBot()).deliver_pending(datetime.now(timezone.utc))
            await UserService(session).delete_profile(1, other.id)
            names = await ListingService(session).delivery_profiles(1, 1)
            self.assertEqual([p["name"] for p in names], ["Основной", "Родителям"])
            self.assertEqual((await self.schedule(session))["deliveries_created"], 0)

    async def test_another_user_cannot_edit_enable_or_delete_a_profile(self):
        async with get_session() as session:
            profile = (await UserService(session).get_profiles(1))[0]
            session.add(BotUser(telegram_id=2))
            await session.commit()
            with self.assertRaises(ValueError):
                await UserService(session).set_profile_enabled(2, profile.id, False)
            with self.assertRaises(ValueError):
                await UserService(session).save_profile(2, profile_id=profile.id, name="Чужой")
            await UserService(session).delete_profile(2, profile.id)
            self.assertIsNotNone(await UserService(session).get_profile(1, profile.id))

    async def test_profile_names_are_unique_for_each_user_case_insensitively(self):
        async with get_session() as session:
            await self.add_profile(session, name="Родителям")
            with self.assertRaises(ValueError):
                await self.add_profile(session, name="родителям")
            self.assertEqual(len(await UserService(session).get_profiles(1)), 2)

    async def test_new_criteria_are_configuration_only_and_start_disabled(self):
        async with get_session() as session:
            await UserService(session).set_notifications(1, False)
            before = (await session.get(BotUser, 1)).notification_cursor_at
            profile = await UserService(session).save_profile(1, name="Новый", city="grodno")
            self.assertFalse(profile.enabled)
            self.assertFalse((await session.get(BotUser, 1)).notifications)
            self.assertEqual((await session.get(BotUser, 1)).notification_cursor_at, before)
            await UserService(session).set_profile_enabled(1, profile.id, True)
            self.assertFalse((await session.get(BotUser, 1)).notifications)

    async def test_first_profile_activation_starts_the_cursor_before_the_first_scan(self):
        async with get_session() as session:
            user = await session.get(BotUser, 1)
            user.notification_cursor_at = None
            user.notification_started_at = None
            await session.commit()
            profile = await UserService(session).save_profile(1, name="Первый", city="grodno", rooms=["2"])
            self.assertIsNone(user.notification_cursor_at)
            await UserService(session).set_profile_enabled(1, profile.id, True)
            self.assertIsNotNone(user.notification_cursor_at)
            await self.listing(session, list_time=datetime.now(timezone.utc))
            self.assertEqual((await self.schedule(session))["deliveries_created"], 1)
