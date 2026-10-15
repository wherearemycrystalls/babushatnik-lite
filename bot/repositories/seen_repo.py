from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from bot.db.models import UserSeen
from bot.repositories.base import BaseRepository


class SeenRepository(BaseRepository[UserSeen]):
    model = UserSeen

    async def mark_seen(self, telegram_id: int, ad_id: int) -> None:
        stmt = (
            insert(UserSeen)
            .values(telegram_id=telegram_id, ad_id=ad_id)
            .on_conflict_do_nothing(index_elements=["telegram_id", "ad_id"])
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def mark_seen_bulk(self, telegram_id: int, ad_ids: list[int]) -> None:
        if not ad_ids:
            return
        stmt = (
            insert(UserSeen)
            .values([{"telegram_id": telegram_id, "ad_id": ad_id} for ad_id in ad_ids])
            .on_conflict_do_nothing(index_elements=["telegram_id", "ad_id"])
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_seen_ids(self, telegram_id: int) -> set[int]:
        result = await self._session.execute(select(UserSeen.ad_id).where(UserSeen.telegram_id == telegram_id))
        return set(result.scalars().all())
