from __future__ import annotations

from aiogram.types import InputMediaPhoto

from kufar_scraper.db.models import Listing


def build_media_group(listing: Listing, caption: str, max_photos: int = 5) -> list[InputMediaPhoto]:
    """
    Build an aiogram InputMediaPhoto list for a listing.
    The caption is attached to the first photo only.
    Returns an empty list if there are no images (caller should send text-only).
    """
    images: list[str] = listing.images or []
    urls = [url for url in images if isinstance(url, str)][:max_photos]

    if not urls:
        return []

    media = []
    for i, url in enumerate(urls):
        media.append(
            InputMediaPhoto(
                media=url,
                caption=caption if i == 0 else None,
                parse_mode="Markdown" if i == 0 else None,
            )
        )
    return media
