from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import UserPreference
from bot.keyboards.callbacks import ProfileAction
from bot.keyboards.factory import KeyboardFactory
from bot.services.user_service import UserService
from bot.utils.formatters import _escape, format_criteria_summary
from kufar_scraper.db.models import CityConfigDB

router = Router(name="criteria")

_PASSTHROUGH = frozenset({"🔍 Просмотр", "🔍 Смотреть квартиры", "⭐ Избранное", "⚙️ Настройки", "⚙️ Поиск", "🏠 В меню"})


def _not_command(message: Message) -> bool:
    return not (message.text or "").startswith("/")


class CriteriaWizard(StatesGroup):
    name = State()
    city = State()
    rooms = State()
    price_min = State()
    price_max = State()
    confirm = State()


async def show_profiles(target, telegram_id, user_service, *, page=0):
    profiles = await user_service.get_profiles(telegram_id)
    page_size = 5
    page = min(max(page, 0), max(0, (len(profiles) - 1) // page_size))
    lines = [f"Наборы критериев: {len(profiles)}", ""]
    for profile in profiles[page * page_size : (page + 1) * page_size]:
        city = profile.city.capitalize() if profile.city else "Любой"
        rooms = ", ".join(profile.rooms) if profile.rooms else "Любая"
        low = f"от ${profile.price_usd_min}" if profile.price_usd_min is not None else ""
        high = f"до ${profile.price_usd_max}" if profile.price_usd_max is not None else ""
        lines.extend(
            [
                f"{profile.name}",
                f"Город: {city}",
                f"Комнатность: {rooms}",
                f"Бюджет: {' '.join(filter(None, [low, high])) or 'Без ограничений'}",
                "",
            ]
        )
    lines.append("Выбери набор для редактирования или добавь новый." if profiles else "Добавь первый набор критериев.")
    lines.append("Включение наборов и запуск поиска — в разделе «Поиск».")
    await target.answer("\n".join(lines), reply_markup=KeyboardFactory.profiles(profiles, page))


@router.message(Command("criteria", "browse"))
@router.message(F.text.in_({"🔍 Просмотр", "🔍 Смотреть квартиры", "🎯 Критерии"}))
async def cmd_criteria(message: Message, state: FSMContext, session: AsyncSession, user_service: UserService):
    await state.clear()
    await user_service.register(message.from_user)
    await show_profiles(message, message.from_user.id, user_service)


@router.callback_query(ProfileAction.filter())
async def profile_action(
    call: CallbackQuery,
    callback_data: ProfileAction,
    state: FSMContext,
    session: AsyncSession,
    user_service: UserService,
):
    await call.answer()
    if not isinstance(call.message, Message):
        return
    data = callback_data
    if data.action == "page":
        await state.clear()
        await show_profiles(call.message, call.from_user.id, user_service, page=data.page)
        return
    if data.action == "add":
        await _start_wizard(call.message, state, session)
        return
    profile = await user_service.get_profile(call.from_user.id, data.profile_id)
    if profile is None:
        await call.message.answer("Набор не найден.")
        return
    if data.action == "edit":
        await _start_wizard(call.message, state, session, profile)
    elif data.action in {"enable", "disable"}:
        await call.message.answer(
            "Включение наборов теперь находится в разделе «Поиск».", reply_markup=KeyboardFactory.main_menu()
        )
    elif data.action == "delete":
        await call.message.answer(
            f"Удалить набор «{profile.name}»?", reply_markup=KeyboardFactory.profile_delete(profile.id)
        )
    elif data.action == "confirm_delete":
        await user_service.delete_profile(call.from_user.id, profile.id)
        await state.clear()
        await show_profiles(call.message, call.from_user.id, user_service)
    else:
        await call.message.answer(
            f"*Набор: {_escape(profile.name, 40)}*\n" + format_criteria_summary(profile),
            parse_mode="Markdown",
            reply_markup=KeyboardFactory.profile_detail(profile),
        )


@router.message(CriteriaWizard.name, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_name(message: Message, state: FSMContext, user_service: UserService):
    name = (message.text or "").strip()
    data = await state.get_data()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        await message.answer("Введи название от 1 до 40 символов без переносов строк.")
        return
    profiles = await user_service.get_profiles(message.from_user.id)
    if any(profile.name.lower() == name.lower() and profile.id != data.get("profile_id") for profile in profiles):
        await message.answer("Набор с таким названием уже есть. Введи другое.")
        return
    await state.update_data(name=name)
    await state.set_state(CriteriaWizard.city)
    await message.answer(
        "Шаг 1/4 — *Выбери город:*",
        reply_markup=KeyboardFactory.criteria_city(data.get("cities", [])),
        parse_mode="Markdown",
    )


@router.message(CriteriaWizard.city, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_city(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    cities: list[str] = (await state.get_data()).get("cities", [])
    normalized = {city.lower(): city for city in cities}
    if text == "🌐 Любой":
        await state.update_data(city=None)
    elif text.lower() in normalized:
        await state.update_data(city=normalized[text.lower()])
    else:
        await message.answer("Пожалуйста, выбери город из кнопок ниже.")
        return
    await state.set_state(CriteriaWizard.rooms)
    await message.answer(
        "Шаг 2/4 — *Количество комнат*\nНажимай нужные цифры, затем *✅ Готово*, или выбери *🌐 Любой*.",
        reply_markup=KeyboardFactory.criteria_rooms(),
        parse_mode="Markdown",
    )


@router.message(CriteriaWizard.rooms, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_rooms(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "🌐 Любой":
        await state.update_data(rooms=None)
        await _ask_price_min(message, state)
        return
    if text == "✅ Готово":
        if not (await state.get_data()).get("rooms"):
            await message.answer("Выбери хотя бы одну комнату или нажми *🌐 Любой*.", parse_mode="Markdown")
            return
        await _ask_price_min(message, state)
        return
    if text in {"1", "2", "3", "4", "5"}:
        rooms = list((await state.get_data()).get("rooms") or [])
        if text in rooms:
            rooms.remove(text)
        else:
            rooms.append(text)
        await state.update_data(rooms=rooms)
        selected = ", ".join(sorted(rooms)) + "-комн." if rooms else "не выбрано"
        await message.answer(
            f"Шаг 2/4 — *Количество комнат*\nВыбрано: {selected}\n\nДобавь ещё или нажми *✅ Готово*.",
            reply_markup=KeyboardFactory.criteria_rooms(),
            parse_mode="Markdown",
        )
        return
    await message.answer("Используй кнопки ниже 👇")


async def _ask_price_min(target: Message, state: FSMContext) -> None:
    await state.set_state(CriteriaWizard.price_min)
    await target.answer(
        "Шаг 3/4 — *Минимальная цена (USD):*", reply_markup=KeyboardFactory.criteria_price(), parse_mode="Markdown"
    )


@router.message(CriteriaWizard.price_min, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_price_min(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "⏭ Пропустить":
        value = None
    elif text.isdigit():
        value = int(text)
    else:
        await message.answer("Выбери цену из кнопок или нажми *⏭ Пропустить*.", parse_mode="Markdown")
        return
    await state.update_data(price_usd_min=value)
    await state.set_state(CriteriaWizard.price_max)
    await message.answer(
        "Шаг 4/4 — *Максимальная цена (USD):*", reply_markup=KeyboardFactory.criteria_price(), parse_mode="Markdown"
    )


@router.message(CriteriaWizard.price_max, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_price_max(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "⏭ Пропустить":
        value = None
    elif text.isdigit():
        value = int(text)
    else:
        await message.answer("Выбери цену из кнопок или нажми *⏭ Пропустить*.", parse_mode="Markdown")
        return
    data = await state.get_data()
    if data.get("price_usd_min") is not None and value is not None and value < data["price_usd_min"]:
        await message.answer("Максимальная цена не может быть меньше минимальной.")
        return
    await state.update_data(price_usd_max=value)
    temp = UserPreference(
        telegram_id=message.from_user.id,
        city=data.get("city"),
        rooms=data.get("rooms"),
        price_usd_min=data.get("price_usd_min"),
        price_usd_max=value,
    )
    await state.set_state(CriteriaWizard.confirm)
    await message.answer(
        "Набор: " + _escape(data.get("name", ""), 40) + "\n\n" + format_criteria_summary(temp) + "\n\nСохранить?",
        reply_markup=KeyboardFactory.criteria_confirm(),
        parse_mode="Markdown",
    )


@router.message(CriteriaWizard.confirm, _not_command, ~F.text.in_(_PASSTHROUGH))
async def step_confirm(message: Message, state: FSMContext, user_service: UserService, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if text == "✅ Сохранить":
        data = await state.get_data()
        try:
            await user_service.save_profile(
                message.from_user.id,
                profile_id=data.get("profile_id"),
                name=data["name"],
                city=data.get("city"),
                rooms=data.get("rooms"),
                price_usd_min=data.get("price_usd_min"),
                price_usd_max=data.get("price_usd_max"),
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.clear()
        await message.answer(
            "✅ Набор сохранён. Управлять его включением можно в разделе «Поиск».",
            reply_markup=KeyboardFactory.main_menu(),
        )
        await show_profiles(message, message.from_user.id, user_service)
    elif text == "↩️ Заново":
        data = await state.get_data()
        profile = (
            await user_service.get_profile(message.from_user.id, data["profile_id"]) if data.get("profile_id") else None
        )
        await _start_wizard(message, state, session, profile)
    else:
        await message.answer("Нажми *✅ Сохранить* или *↩️ Заново*.", parse_mode="Markdown")


async def _start_wizard(target: Message, state: FSMContext, session: AsyncSession, profile=None) -> None:
    await state.clear()
    cities = list(
        (
            await session.execute(
                select(CityConfigDB.name).where(CityConfigDB.enabled.is_(True)).order_by(CityConfigDB.name)
            )
        )
        .scalars()
        .all()
    )
    await state.set_state(CriteriaWizard.name)
    await state.update_data(cities=cities, profile_id=profile.id if profile else None)
    await target.answer(
        "Введи название набора поиска:", reply_markup=KeyboardFactory.profile_name(profile.name if profile else None)
    )
