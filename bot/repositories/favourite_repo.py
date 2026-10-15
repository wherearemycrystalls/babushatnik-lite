from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.db.models import UserFavourite
from bot.repositories.base import BaseRepository


class FavouriteRepository(BaseRepository[UserFavourite]):
    model = UserFavourite

    async def add(self, telegram_id: int, ad_id: int) -> UserFavourite:
        fav = UserFavourite(telegram_id=telegram_id, ad_id=ad_id)
        self._session.add(fav)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            result = await self._session.execute(
                select(UserFavourite).where(
                    UserFavourite.telegram_id == telegram_id,
                    UserFavourite.ad_id == ad_id,
                )
            )
            fav = result.scalar_one()
        return fav

    async def remove(self, telegram_id: int, ad_id: int) -> None:
        result = await self._session.execute(
            select(UserFavourite).where(
                UserFavourite.telegram_id == telegram_id,
                UserFavourite.ad_id == ad_id,
            )
        )
        fav = result.scalar_one_or_none()
        if fav:
            await self._session.delete(fav)
            await self._session.flush()

    async def list_for_user(self, telegram_id: int) -> list[UserFavourite]:
        result = await self._session.execute(
            select(UserFavourite)
            .where(UserFavourite.telegram_id == telegram_id)
            .order_by(UserFavourite.saved_at.desc())
        )
        return list(result.scalars().all())

    async def exists(self, telegram_id: int, ad_id: int) -> bool:
        result = await self._session.execute(
            select(UserFavourite).where(
                UserFavourite.telegram_id == telegram_id,
                UserFavourite.ad_id == ad_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_ad_ids_for_user(self, telegram_id: int) -> list[int]:
        result = await self._session.execute(
            select(UserFavourite.ad_id).where(UserFavourite.telegram_id == telegram_id)
        )
        return list(result.scalars().all())
