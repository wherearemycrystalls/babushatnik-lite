import os
import unittest
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Message, Update
from sqlalchemy import delete, select, update

from bot.app import DependencyMiddleware
from bot.config import BotConfig
from bot.db.models import BotUser, ListingDelivery, NotificationDelivery, UserFavourite, UserPreference, UserSeen
from bot.keyboards.callbacks import ApartmentAction, FavouritesAction, ProfileAction, SearchAction
from bot.services.listing_service import ListingService
from kufar_scraper.db.client import dispose_engine, get_session, init_engine, upsert_listing
from kufar_scraper.db.models import Listing
from kufar_scraper.models.schemas import ParsedListing

URL = os.getenv("TEST_DATABASE_URL")


class RecordingTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(timezone.utc),
                chat={"id": method.chat_id, "type": "private"},
                text=method.text,
            )
        return True


@unittest.skipUnless(URL, "set TEST_DATABASE_URL for PostgreSQL tests")
class BotFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_push_card_favourite_archive_clear_and_navigation(self):
        from bot.handlers import criteria, fallback, favourites, settings, start

        init_engine(URL)
        session = RecordingTelegram()
        bot = Bot("123456:fake_test_token", session=session)
        dp = Dispatcher(storage=MemoryStorage())
        dp.update.outer_middleware(
            DependencyMiddleware(get_session, BotConfig(bot_token="123456:fake_test_token", database_url=URL))
        )
        for router in [start.router, criteria.router, favourites.router, settings.router, fallback.router]:
            dp.include_router(router)
        try:
            async with get_session() as db:
                for model in [
                    ListingDelivery,
                    NotificationDelivery,
                    UserSeen,
                    UserFavourite,
                    UserPreference,
                    BotUser,
                    Listing,
                ]:
                    await db.execute(delete(model))
                await db.commit()
                await upsert_listing(
                    db,
                    ParsedListing(
                        ad_id=1001, city="grodno", rooms="2", price_usd=300, list_time=datetime.now(timezone.utc)
                    ),
                )
            serial = 0

            async def message(text):
                nonlocal serial
                serial += 1
                await dp.feed_update(
                    bot,
                    Update.model_validate(
                        {
                            "update_id": serial,
                            "message": {
                                "message_id": serial,
                                "date": int(datetime.now(timezone.utc).timestamp()),
                                "chat": {"id": 1, "type": "private"},
                                "from": {"id": 1, "is_bot": False, "first_name": "Test"},
                                "text": text,
                            },
                        }
                    ),
                )

            async def callback(data):
                nonlocal serial
                serial += 1
                await dp.feed_update(
                    bot,
                    Update.model_validate(
                        {
                            "update_id": serial,
                            "callback_query": {
                                "id": str(serial),
                                "chat_instance": "test",
                                "from": {"id": 1, "is_bot": False, "first_name": "Test"},
                                "data": data,
                                "message": {
                                    "message_id": 1,
                                    "date": int(datetime.now(timezone.utc).timestamp()),
                                    "chat": {"id": 1, "type": "private"},
                                    "text": "Apartment",
                                },
                            },
                        }
                    ),
                )

            await message("/criteria")  # registration also works without /start
            await callback(ProfileAction(action="add").pack())
            for text in ["Для себя", "Grodno", "2", "✅ Готово", "⏭ Пропустить", "400", "✅ Сохранить"]:
                await message(text)
            async with get_session() as db:
                prefs = await db.scalar(select(UserPreference).where(UserPreference.telegram_id == 1))
                self.assertEqual((prefs.city, prefs.rooms, prefs.price_usd_max), ("grodno", ["2"], 400))
                self.assertFalse(prefs.enabled)
            # Criteria lists the saved settings and has no search switches.
            before = len(session.requests)
            await message("🎯 Критерии")
            panel = next(method for method in session.requests[before:] if isinstance(method, SendMessage))
            self.assertIn("Город: Grodno", panel.text)
            self.assertIn("Комнатность: 2", panel.text)
            self.assertIn("до $400", panel.text)
            self.assertFalse(
                any(
                    (button.callback_data or "").startswith("search:")
                    for row in panel.reply_markup.inline_keyboard
                    for button in row
                )
            )
            async with get_session() as db:
                self.assertIsNone((await db.get(BotUser, 1)).notification_started_at)
            await callback(ApartmentAction(action="save", ad_id=1001).pack())
            await callback(ApartmentAction(action="save", ad_id=1001).pack())
            before = len(session.requests)
            await message("⚙️ Поиск")
            search_panel = next(method for method in session.requests[before:] if isinstance(method, SendMessage))
            self.assertFalse(
                any(
                    (button.callback_data or "").startswith("profile:")
                    for row in search_panel.reply_markup.inline_keyboard
                    for button in row
                )
            )
            await callback(SearchAction(action="disable", profile_id=prefs.id).pack())
            async with get_session() as db:
                self.assertFalse((await db.get(UserPreference, prefs.id)).enabled)
            await callback(SearchAction(action="enable", profile_id=prefs.id).pack())
            await callback(ProfileAction(action="add").pack())
            for text in ["Родителям", "Grodno", "1", "✅ Готово", "⏭ Пропустить", "300", "✅ Сохранить"]:
                await message(text)
            await message("/criteria")
            await callback(ProfileAction(action="edit", profile_id=prefs.id).pack())
            await message("⭐ Избранное")  # leave a wizard through main navigation
            async with get_session() as db:
                self.assertEqual(await ListingService(db).favourite_counts(1), (1, 0))
                await db.execute(update(Listing).values(is_source_active=False))
                await db.commit()
            await callback(FavouritesAction(action="page", archived=True).pack())
            await callback(ApartmentAction(action="view", ad_id=1001).pack())
            await callback(FavouritesAction(action="clear", archived=True).pack())
            async with get_session() as db:
                self.assertEqual(await ListingService(db).favourite_counts(1), (0, 1))
            await callback(FavouritesAction(action="confirm", archived=True).pack())
            async with get_session() as db:
                self.assertEqual(await ListingService(db).favourite_counts(1), (0, 0))
            await message("⚙️ Поиск")
            await message("⏸ Приостановить поиск")
            await message("⏸ Приостановить поиск")
            async with get_session() as db:
                self.assertFalse((await db.get(BotUser, 1)).notifications)
            await message("▶️ Возобновить поиск")
            await callback(ApartmentAction(action="check", ad_id=1001).pack())
            self.assertNotIn(
                "Проверить актуальность", str([getattr(method, "reply_markup", None) for method in session.requests])
            )
            await message("❤️")  # an old swipe keyboard gets the new menu
            texts = "\n".join(str(getattr(method, "text", "")) for method in session.requests)
            self.assertIn("Архив избранного: 1", texts)
            self.assertIn("Набор сохранён", texts)
            self.assertNotIn("Tinder", texts)
        finally:
            await dp.storage.close()
            await bot.session.close()
            await dispose_engine()
