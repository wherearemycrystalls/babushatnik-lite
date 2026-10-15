from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.factory import KeyboardFactory

router = Router(name="fallback")


@router.callback_query()
async def stale_callback(call: CallbackQuery):
    await call.answer("Используй кнопки под актуальной карточкой или главное меню.")


@router.message()
async def fallback(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Настрой критерии — новые квартиры будут приходить сюда автоматически.",
        reply_markup=KeyboardFactory.main_menu(),
    )
