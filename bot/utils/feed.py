from aiogram.exceptions import TelegramBadRequest

from bot.keyboards.factory import KeyboardFactory
from bot.utils.formatters import _escape, format_card, format_full_detail


def profile_blocks(profiles):
    """Keep every matched name, splitting exceptionally large collections at name boundaries."""
    blocks = []
    current = "Подходит наборам: "
    for profile in profiles or []:
        name = _escape(profile["name"], 40)
        if len(current) + len(name) + 2 > 3000:
            blocks.append(current)
            current = "Подходит наборам (продолжение): "
        current += (", " if not current.endswith(": ") else "") + name
    if profiles:
        blocks.append(current)
    return blocks


async def send_listing(bot, chat_id, listing, *, matched_profiles=None):
    keyboard = KeyboardFactory.apartment(listing.ad_id, listing.link)
    blocks = profile_blocks(matched_profiles)
    suffix = "\n\n" + blocks[0] if blocks else ""
    card = format_card(listing) + suffix
    message = None
    if listing.images and len(card) <= 1024:
        try:
            message = await bot.send_photo(
                chat_id=chat_id, photo=listing.images[0], caption=card, reply_markup=keyboard, parse_mode="Markdown"
            )
        except TelegramBadRequest:
            pass
    if message is None:
        detail = format_full_detail(listing) + suffix
        message = await bot.send_message(
            chat_id=chat_id,
            text=detail if len(detail) <= 4000 else card,
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    for block in blocks[1:]:
        await bot.send_message(chat_id=chat_id, text=block, parse_mode="Markdown")
    return message
