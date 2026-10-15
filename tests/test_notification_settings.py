import unittest

from bot.keyboards.callbacks import ApartmentAction, FavouritesAction
from bot.keyboards.factory import KeyboardFactory


class FeedKeyboardTests(unittest.TestCase):
    def test_main_menu_has_search_and_favourites_without_swipes(self):
        text = str(KeyboardFactory.main_menu().model_dump())
        self.assertIn("Критерии", text)
        self.assertIn("Избранное", text)
        self.assertNotIn("Просмотр", text)
        self.assertNotIn("❤️", text)
        self.assertNotIn("👎", text)

    def test_settings_only_pause_and_reset(self):
        for enabled in [False, True]:
            text = str(KeyboardFactory.settings_menu(enabled).model_dump())
            self.assertNotIn("Интервал", text)
            self.assertNotIn("просмотренных", text)

    def test_inline_callbacks_round_trip(self):
        apartment = ApartmentAction(action="save", ad_id=1050439878)
        self.assertEqual(ApartmentAction.unpack(apartment.pack()), apartment)
        archive = FavouritesAction(action="page", archived=True, page=200)
        self.assertEqual(FavouritesAction.unpack(archive.pack()), archive)

    def test_no_manual_availability_buttons_in_cards_or_favourites(self):
        for keyboard in [KeyboardFactory.apartment(1), KeyboardFactory.favourite_detail(1)]:
            self.assertNotIn("Проверить актуальность", str(keyboard.model_dump()))
            self.assertFalse(
                any(button.callback_data == "apt:check:1" for row in keyboard.inline_keyboard for button in row)
            )

    def test_search_and_criteria_offer_disjoint_actions(self):
        from types import SimpleNamespace

        profile = SimpleNamespace(id=1, name="Тест", enabled=True)
        management = KeyboardFactory.profiles([profile])
        detail = KeyboardFactory.profile_detail(profile)
        search = KeyboardFactory.search([profile], True)
        for keyboard in [management, detail]:
            self.assertTrue(
                all(
                    not (button.callback_data or "").startswith("search:")
                    for row in keyboard.inline_keyboard
                    for button in row
                )
            )
        self.assertTrue(
            all(
                not (button.callback_data or "").startswith("profile:")
                for row in search.inline_keyboard
                for button in row
            )
        )
