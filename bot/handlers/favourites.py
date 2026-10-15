from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.callbacks import ApartmentAction, FavouritesAction
from bot.keyboards.factory import KeyboardFactory
from bot.services.listing_service import ListingService
from bot.services.user_service import UserService
from bot.utils.feed import profile_blocks
from bot.utils.formatters import format_card, format_full_detail

router = Router(name="favourites")


async def show_favourites(message, telegram_id, service, *, archived=False, page=0):
    rows, total, page = await service.favourite_page(telegram_id, archived, page)
    active, archive = await service.favourite_counts(telegram_id)
    title = "Архив избранного" if archived else "Актуальное избранное"
    text = f"{title}: {total}\n\n"
    if rows:
        text += "Выбери квартиру, чтобы открыть карточку."
    elif archived:
        text += "Здесь будут сохранённые квартиры, которых больше нет в выдаче Kufar."
    else:
        text += "Сохраняй подходящие квартиры кнопкой «В избранное» под карточкой в ленте."
    await message.answer(
        text,
        reply_markup=KeyboardFactory.favourites(
            rows, archived=archived, page=page, total=total, active_count=active, archive_count=archive
        ),
    )


@router.message(Command("favourites"))
@router.message(F.text == "⭐ Избранное")
async def cmd_favourites(message: Message, state: FSMContext, listing_service: ListingService):
    await state.clear()
    await show_favourites(message, message.from_user.id, listing_service)


@router.callback_query(FavouritesAction.filter())
async def favourites_action(call: CallbackQuery, callback_data: FavouritesAction, listing_service: ListingService):
    await call.answer()
    if not isinstance(call.message, Message):
        return
    data = callback_data
    if data.action == "clear":
        scope = "квартиры в архиве" if data.archived else "актуальные квартиры в избранном"
        await call.message.answer(
            f"Удалить все сохранённые {scope}?", reply_markup=KeyboardFactory.clear_favourites(data.archived)
        )
        return
    if data.action == "confirm":
        count = await listing_service.clear_favourites(call.from_user.id, archived=data.archived)
        await call.message.answer(f"Удалено квартир: {count}.")
    await show_favourites(call.message, call.from_user.id, listing_service, archived=data.archived, page=data.page)


@router.callback_query(ApartmentAction.filter())
async def apartment_action(
    call: CallbackQuery,
    callback_data: ApartmentAction,
    listing_service: ListingService,
    user_service: UserService,
):
    if not isinstance(call.message, Message):
        await call.answer()
        return
    data = callback_data
    listing = await listing_service.get_listing(data.ad_id)
    if listing is None:
        await call.answer("Объявление не найдено", show_alert=True)
        return
    if data.action == "save":
        await user_service.register(call.from_user)
        await listing_service.save_favourite(call.from_user.id, data.ad_id)
        await call.answer("Сохранено в избранное")
        # Saving is idempotent, including clicks on old messages.
        try:
            await call.message.edit_reply_markup(
                reply_markup=KeyboardFactory.apartment(data.ad_id, listing.link, saved=True)
            )
        except TelegramBadRequest:
            pass
        return
    if data.action == "remove":
        await listing_service.remove_favourite(call.from_user.id, data.ad_id)
        await call.answer("Удалено из избранного")
        await show_favourites(call.message, call.from_user.id, listing_service)
        return
    if data.action == "view":
        if not await listing_service.is_favourite(call.from_user.id, data.ad_id):
            await call.answer("Квартира уже удалена из избранного")
            return
        await call.answer()
        note = (
            "\n\nАрхив: объявление снято или скрыто из выдачи."
            if not listing.is_source_active or listing.is_hidden_by_admin
            else ""
        )
        blocks = profile_blocks(await listing_service.delivery_profiles(call.from_user.id, data.ad_id))
        suffix = "\n\n" + blocks[0] if blocks else ""
        text = format_full_detail(listing) + note + suffix
        if len(text) > 4096:
            text = format_card(listing) + note + suffix
        await call.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=KeyboardFactory.favourite_detail(data.ad_id),
            disable_web_page_preview=True,
        )
        for block in blocks[1:]:
            await call.message.answer(block, parse_mode="Markdown")
        return
    await call.answer("Эта кнопка больше не используется.")
