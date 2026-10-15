from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.callbacks import SearchAction
from bot.keyboards.factory import KeyboardFactory
from bot.services.user_service import UserService

router = Router(name="settings")


async def show_search(target, telegram_id, user_service, *, page=0):
    user = await user_service._users.get(telegram_id)
    enabled = user.notifications if user else True
    profiles = await user_service.get_profiles(telegram_id)
    selected = sum(profile.enabled for profile in profiles)
    text = (
        "Поиск включён." if enabled else "Поиск приостановлен."
    ) + f" Выбрано наборов: {selected} из {len(profiles)}.\n\n"
    text += (
        "Включай нужные наборы переключателями ниже. Каждая новая квартира приходит один раз с названиями совпавших наборов.\n"
        if profiles
        else "Сначала создай набор в разделе «Критерии».\n"
    )
    if profiles and not selected:
        text += "Сейчас все наборы выключены — выбери хотя бы один.\n"
    text += "Изменять параметры наборов можно в разделе «Критерии»."
    await target.answer(text, reply_markup=KeyboardFactory.search(profiles, enabled, page))


@router.message(Command("settings", "search"))
@router.message(F.text.in_({"⚙️ Настройки", "⚙️ Поиск"}))
async def cmd_settings(message: Message, state: FSMContext, user_service: UserService):
    await state.clear()
    await user_service.register(message.from_user)
    await show_search(message, message.from_user.id, user_service)


@router.callback_query(SearchAction.filter())
async def search_action(call: CallbackQuery, callback_data: SearchAction, state: FSMContext, user_service: UserService):
    await call.answer()
    if not isinstance(call.message, Message):
        return
    data = callback_data
    await state.clear()
    if data.action in {"pause", "resume"}:
        await user_service.set_notifications(call.from_user.id, data.action == "resume")
    elif data.action in {"enable", "disable"}:
        try:
            await user_service.set_profile_enabled(call.from_user.id, data.profile_id, data.action == "enable")
        except ValueError:
            await call.message.answer("Набор не найден.")
    await show_search(call.message, call.from_user.id, user_service, page=data.page)


@router.message(
    F.text.in_({"⏸ Приостановить поиск", "▶️ Возобновить поиск", "🔔 Вкл. уведомления", "🔕 Выкл. уведомления"})
)
async def toggle_notifications(message: Message, state: FSMContext, user_service: UserService):
    await state.clear()
    await user_service.set_notifications(
        message.from_user.id, message.text in {"▶️ Возобновить поиск", "🔔 Вкл. уведомления"}
    )
    await show_search(message, message.from_user.id, user_service)


@router.message(F.text == "🏠 В меню")
async def settings_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню", reply_markup=KeyboardFactory.main_menu())
