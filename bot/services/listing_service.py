from __future__ import annotations

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import ListingDelivery, UserFavourite, UserPreference
from bot.repositories.favourite_repo import FavouriteRepository
from kufar_scraper.db.models import Listing


class ListingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._favourites = FavouriteRepository(session)

    async def get_listing(self, ad_id: int, *, refresh=False) -> Listing | None:
        return await self._session.get(Listing, ad_id, populate_existing=refresh)

    async def save_favourite(self, telegram_id: int, ad_id: int) -> None:
        await self._session.execute(
            insert(UserFavourite)
            .values(telegram_id=telegram_id, ad_id=ad_id)
            .on_conflict_do_nothing(index_elements=["telegram_id", "ad_id"])
        )
        await self._session.commit()

    async def remove_favourite(self, telegram_id, ad_id):
        await self._session.execute(
            delete(UserFavourite).where(UserFavourite.telegram_id == telegram_id, UserFavourite.ad_id == ad_id)
        )
        await self._session.commit()

    async def favourite_page(self, telegram_id, archived=False, page=0, page_size=10):
        condition = favourite_archive_clause() if archived else visible_listing_clause()
        query = (
            select(UserFavourite, Listing)
            .join(Listing, Listing.ad_id == UserFavourite.ad_id)
            .where(UserFavourite.telegram_id == telegram_id, condition)
        )
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        page = min(max(0, page), max(0, (total - 1) // page_size))
        rows = (
            await self._session.execute(
                query.order_by(UserFavourite.saved_at.desc(), UserFavourite.id.desc())
                .offset(page * page_size)
                .limit(page_size)
            )
        ).all()
        return rows, total, page

    async def favourite_counts(self, telegram_id):
        active = await self._session.scalar(
            select(func.count())
            .select_from(UserFavourite)
            .join(Listing, Listing.ad_id == UserFavourite.ad_id)
            .where(UserFavourite.telegram_id == telegram_id, visible_listing_clause())
        )
        archived = await self._session.scalar(
            select(func.count())
            .select_from(UserFavourite)
            .join(Listing, Listing.ad_id == UserFavourite.ad_id)
            .where(UserFavourite.telegram_id == telegram_id, favourite_archive_clause())
        )
        return active, archived

    async def clear_favourites(self, telegram_id, *, archived):
        ids = select(Listing.ad_id).where(favourite_archive_clause() if archived else visible_listing_clause())
        result = await self._session.execute(
            delete(UserFavourite).where(UserFavourite.telegram_id == telegram_id, UserFavourite.ad_id.in_(ids))
        )
        await self._session.commit()
        return result.rowcount

    async def is_favourite(self, telegram_id, ad_id):
        return await self._favourites.exists(telegram_id, ad_id)

    async def delivery_profiles(self, telegram_id, ad_id):
        return (
            await self._session.scalar(
                select(ListingDelivery.matched_profiles).where(
                    ListingDelivery.telegram_id == telegram_id,
                    ListingDelivery.ad_id == ad_id,
                    ListingDelivery.status == "delivered",
                )
            )
            or []
        )


def _build_filter_clauses(prefs: UserPreference) -> list:
    clauses = []
    if prefs.city:
        clauses.append(Listing.city == prefs.city)
    if prefs.rooms:
        # Listing.rooms is String(50) — use string equality
        clauses.append(Listing.rooms.in_(prefs.rooms))
    if prefs.price_usd_min is not None:
        clauses.append(Listing.price_usd >= prefs.price_usd_min)
    if prefs.price_usd_max is not None:
        clauses.append(Listing.price_usd <= prefs.price_usd_max)
    return clauses


def visible_listing_clause():
    return and_(Listing.is_source_active.is_(True), Listing.is_hidden_by_admin.is_(False))


def favourite_archive_clause():
    return ~visible_listing_clause()
