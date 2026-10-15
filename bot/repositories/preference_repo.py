from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from bot.db.models import UserPreference
from bot.repositories.base import BaseRepository


class PreferenceRepository(BaseRepository[UserPreference]):
    model = UserPreference

    async def get_for_user(self, telegram_id: int) -> UserPreference | None:
        result = await self._session.execute(
            select(UserPreference).where(UserPreference.telegram_id == telegram_id).order_by(UserPreference.id).limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert(self, telegram_id: int, **fields: Any) -> UserPreference:
        prefs = await self.get_for_user(telegram_id)
        if prefs is None:
            prefs = UserPreference(telegram_id=telegram_id, **fields)
            self._session.add(prefs)
        else:
            for key, value in fields.items():
                setattr(prefs, key, value)
            prefs.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return prefs
