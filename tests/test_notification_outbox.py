import unittest
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendPhoto

from bot.utils.feed import send_listing
from kufar_scraper.db.models import Listing


class FeedMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_photo_falls_back_to_an_apartment_message_with_buttons(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = TelegramBadRequest(
            method=SendPhoto(chat_id=42, photo="https://example.com/a.jpg"), message="bad image"
        )
        listing = Listing(ad_id=1, city="grodno", images=["https://example.com/a.jpg"])
        await send_listing(bot, 42, listing)
        bot.send_message.assert_awaited_once()
        self.assertIn("квартира", bot.send_message.call_args.kwargs["text"])
        self.assertIsNotNone(bot.send_message.call_args.kwargs["reply_markup"])

    async def test_rate_limit_does_not_trigger_a_second_send_as_text(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = TelegramRetryAfter(
            method=SendPhoto(chat_id=42, photo="https://example.com/a.jpg"), message="slow down", retry_after=30
        )
        listing = Listing(ad_id=1, city="grodno", images=["https://example.com/a.jpg"])
        with self.assertRaises(TelegramRetryAfter):
            await send_listing(bot, 42, listing)
        bot.send_message.assert_not_awaited()

    async def test_long_profile_names_preserve_all_names_and_telegram_limits(self):
        from bot.utils.feed import profile_blocks

        bot = AsyncMock()
        profiles = [{"id": i, "name": f"Набор {i} " + "*" * 30} for i in range(100)]
        blocks = profile_blocks(profiles)
        self.assertTrue(all(len(block) <= 3000 for block in blocks))
        listing = Listing(ad_id=1, city="grodno", images=["https://example.com/photo.jpg"])
        await send_listing(bot, 42, listing, matched_profiles=profiles)
        bot.send_photo.assert_not_awaited()
        texts = [call.kwargs["text"] for call in bot.send_message.call_args_list]
        self.assertTrue(all(len(text) <= 4096 for text in texts))
        self.assertIn("Набор 99", "\n".join(texts))
