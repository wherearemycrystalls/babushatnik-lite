from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from bot.db.models import BotUser
from bot.repositories.base import BaseRepository


class UserRepository(BaseRepository[BotUser]):
    model = BotUser

    async def get_or_create(self, telegram_id: int, **defaults: str | None) -> tuple[BotUser, bool]:
        user = await self._session.get(BotUser, telegram_id)
        if user is not None:
            return user, False
        user = BotUser(telegram_id=telegram_id, **defaults)
        self._session.add(user)
        await self._session.flush()
        return user, True

    async def upsert(self, telegram_id: int, **fields: str | None) -> BotUser:
        """Update profile fields (username, first_name, language_code) on every interaction."""
        user = await self._session.get(BotUser, telegram_id)
        if user is None:
            user = BotUser(telegram_id=telegram_id, **fields)
            self._session.add(user)
        else:
            for key, value in fields.items():
                setattr(user, key, value)
            user.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return user

    async def set_active(self, telegram_id: int, active: bool) -> None:
        user = await self._session.get(BotUser, telegram_id)
        if user:
            user.is_active = active
            user.updated_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def get_active_with_notifications(self) -> list[BotUser]:
        result = await self._session.execute(
            select(BotUser).where(BotUser.is_active.is_(True), BotUser.notifications.is_(True))
        )
        return list(result.scalars().all())
