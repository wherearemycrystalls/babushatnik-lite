from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.keyboards.callbacks import ApartmentAction, FavouritesAction, ProfileAction, SearchAction


class KeyboardFactory:
    # ── Main navigation ────────────────────────────────────────────────────────

    @staticmethod
    def main_menu() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎯 Критерии"), KeyboardButton(text="⭐ Избранное")],
                [KeyboardButton(text="⚙️ Поиск")],
            ],
            resize_keyboard=True,
        )

    # ── Criteria — pre-wizard screens ─────────────────────────────────────────

    @staticmethod
    def criteria_existing() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔄 Настроить заново")],
                [KeyboardButton(text="🏠 В меню")],
            ],
            resize_keyboard=True,
        )

    # ── Criteria wizard ────────────────────────────────────────────────────────

    @staticmethod
    def criteria_city(cities: list[str]) -> ReplyKeyboardMarkup:
        buttons = [KeyboardButton(text=c.capitalize()) for c in cities]
        buttons.append(KeyboardButton(text="🌐 Любой"))
        rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
        return ReplyKeyboardMarkup(
            keyboard=rows,
            resize_keyboard=True,
            input_field_placeholder="Выбери город…",
        )

    @staticmethod
    def criteria_rooms() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=str(i)) for i in range(1, 4)],
                [KeyboardButton(text="4"), KeyboardButton(text="5"), KeyboardButton(text="✅ Готово")],
                [KeyboardButton(text="🌐 Любой")],
            ],
            resize_keyboard=True,
            input_field_placeholder="Выбери комнатность…",
        )

    @staticmethod
    def criteria_price() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="100"), KeyboardButton(text="150"), KeyboardButton(text="200")],
                [KeyboardButton(text="250"), KeyboardButton(text="300"), KeyboardButton(text="400")],
                [KeyboardButton(text="500"), KeyboardButton(text="600"), KeyboardButton(text="⏭ Пропустить")],
            ],
            resize_keyboard=True,
            input_field_placeholder="Выбери или введи сумму…",
        )

    @staticmethod
    def criteria_confirm() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Сохранить"), KeyboardButton(text="↩️ Заново")],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def apartment(ad_id, link=None, *, saved=False):
        rows = [
            [
                InlineKeyboardButton(
                    text="⭐ Сохранено" if saved else "⭐ В избранное",
                    callback_data=ApartmentAction(action="save", ad_id=ad_id).pack(),
                )
            ],
        ]
        if link and link.startswith(("https://", "http://")):
            rows.append([InlineKeyboardButton(text="Открыть на Kufar", url=link)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def favourites(rows, *, archived, page, total, active_count, archive_count):
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{page * 10 + i}. {listing.rooms or '?'} комн. · {listing.price_usd or '—'} USD",
                    callback_data=ApartmentAction(action="view", ad_id=listing.ad_id).pack(),
                )
            ]
            for i, (_, listing) in enumerate(rows, 1)
        ]
        navigation = []
        if page:
            navigation.append(
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=FavouritesAction(action="page", archived=archived, page=page - 1).pack(),
                )
            )
        if (page + 1) * 10 < total:
            navigation.append(
                InlineKeyboardButton(
                    text="Далее →",
                    callback_data=FavouritesAction(action="page", archived=archived, page=page + 1).pack(),
                )
            )
        if navigation:
            buttons.append(navigation)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Актуальные ({active_count})", callback_data=FavouritesAction(action="page").pack()
                ),
                InlineKeyboardButton(
                    text=f"Архив ({archive_count})", callback_data=FavouritesAction(action="page", archived=True).pack()
                ),
            ]
        )
        if total:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="Очистить архив" if archived else "Очистить избранное",
                        callback_data=FavouritesAction(action="clear", archived=archived).pack(),
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def clear_favourites(archived):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, очистить", callback_data=FavouritesAction(action="confirm", archived=archived).pack()
                    ),
                    InlineKeyboardButton(
                        text="Отмена", callback_data=FavouritesAction(action="page", archived=archived).pack()
                    ),
                ]
            ]
        )

    @staticmethod
    def favourite_detail(ad_id):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Удалить из избранного", callback_data=ApartmentAction(action="remove", ad_id=ad_id).pack()
                    )
                ],
                [InlineKeyboardButton(text="К избранному", callback_data=FavouritesAction(action="page").pack())],
            ]
        )

    @staticmethod
    def settings_menu(notifications_on, **_):
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸ Приостановить поиск" if notifications_on else "▶️ Возобновить поиск")],
                [KeyboardButton(text="🏠 В меню")],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def profiles(profiles, page=0):
        page_size = 5
        page = min(max(page, 0), max(0, (len(profiles) - 1) // page_size))
        rows = []
        for profile in profiles[page * page_size : (page + 1) * page_size]:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=profile.name,
                        callback_data=ProfileAction(action="view", profile_id=profile.id).pack(),
                    ),
                ]
            )
        nav = []
        if page:
            nav.append(
                InlineKeyboardButton(text="← Назад", callback_data=ProfileAction(action="page", page=page - 1).pack())
            )
        if (page + 1) * page_size < len(profiles):
            nav.append(
                InlineKeyboardButton(text="Далее →", callback_data=ProfileAction(action="page", page=page + 1).pack())
            )
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="➕ Добавить набор", callback_data=ProfileAction(action="add").pack())])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def profile_detail(profile):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Редактировать", callback_data=ProfileAction(action="edit", profile_id=profile.id).pack()
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Удалить набор", callback_data=ProfileAction(action="delete", profile_id=profile.id).pack()
                    )
                ],
                [InlineKeyboardButton(text="К наборам", callback_data=ProfileAction(action="page").pack())],
            ]
        )

    @staticmethod
    def profile_delete(profile_id):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, удалить",
                        callback_data=ProfileAction(action="confirm_delete", profile_id=profile_id).pack(),
                    ),
                    InlineKeyboardButton(
                        text="Отмена", callback_data=ProfileAction(action="view", profile_id=profile_id).pack()
                    ),
                ]
            ]
        )

    @staticmethod
    def profile_name(current=None):
        rows = [[KeyboardButton(text=current)]] if current else []
        rows.append([KeyboardButton(text="🏠 В меню")])
        return ReplyKeyboardMarkup(
            keyboard=rows, resize_keyboard=True, input_field_placeholder="Например: Для себя или Родителям"
        )

    @staticmethod
    def search(profiles, notifications_on, page=0):
        page_size = 5
        page = min(max(page, 0), max(0, (len(profiles) - 1) // page_size))
        rows = [
            [
                InlineKeyboardButton(
                    text=("✅ " if profile.enabled else "⏸ ")
                    + profile.name
                    + (" — выключить" if profile.enabled else " — включить"),
                    callback_data=SearchAction(
                        action="disable" if profile.enabled else "enable", profile_id=profile.id, page=page
                    ).pack(),
                )
            ]
            for profile in profiles[page * page_size : (page + 1) * page_size]
        ]
        nav = []
        if page:
            nav.append(
                InlineKeyboardButton(text="← Назад", callback_data=SearchAction(action="page", page=page - 1).pack())
            )
        if (page + 1) * page_size < len(profiles):
            nav.append(
                InlineKeyboardButton(text="Далее →", callback_data=SearchAction(action="page", page=page + 1).pack())
            )
        if nav:
            rows.append(nav)
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏸ Приостановить весь поиск" if notifications_on else "▶️ Запустить поиск",
                    callback_data=SearchAction(action="pause" if notifications_on else "resume", page=page).pack(),
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)
