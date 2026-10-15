from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.factory import KeyboardFactory
from bot.services.user_service import UserService

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, user_service: UserService):
    await state.clear()
    await user_service.register(message.from_user)
    await message.answer(
        "Привет! Я ищу новые квартиры на Kufar по твоим критериям.\n\n"
        "1. Создай наборы поиска: название, город, комнаты и бюджет.\n"
        "2. Новые совпадения будут приходить сюда отдельными карточками.\n"
        "3. Сохраняй подходящие в избранное. Снятые объявления попадут в его архив.\n\n"
        "Начни с кнопки «Критерии».",
        reply_markup=KeyboardFactory.main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "/criteria — критерии поиска\n/favourites — актуальные квартиры и архив\n"
        "/settings — приостановить или возобновить поиск\n/start — главное меню\n\n"
        "Присылаю только объявления, появившиеся после настройки поиска. "
        "Под карточкой можно сохранить квартиру. В карточке указаны все совпавшие наборы поиска. "
        "Изменение даты известного объявления не приводит к повторной отправке."
    )
