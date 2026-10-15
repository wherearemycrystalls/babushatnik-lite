from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import types
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import BotUser, ListingDelivery, UserPreference
from bot.repositories.preference_repo import PreferenceRepository
from bot.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._prefs = PreferenceRepository(session)
        self._session = session

    async def register(self, tg_user: types.User) -> BotUser:
        """Upsert user record; called on every incoming message."""
        user = await self._users.upsert(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            language_code=tg_user.language_code,
        )
        user.is_active = True
        await self._session.commit()
        return user

    async def get_preferences(self, telegram_id: int) -> UserPreference | None:
        return await self._prefs.get_for_user(telegram_id)

    async def save_preferences(self, telegram_id: int, **fields: Any) -> UserPreference:
        user = await self._locked_user(telegram_id)
        prefs = await self._prefs.upsert(telegram_id, **fields)
        prefs.started_at = datetime.now(timezone.utc)
        if user:
            user.notifications = True
            user.notification_cursor_at = datetime.now(timezone.utc)
            user.notification_started_at = user.notification_cursor_at
        await self._cancel_open_deliveries(telegram_id)
        await self._session.commit()
        return prefs

    async def set_notifications(self, telegram_id: int, enabled: bool) -> bool:
        user = await self._locked_user(telegram_id)
        if user is None:
            return False
        if user.notifications == enabled:
            await self._session.commit()
            return enabled
        user.notifications = enabled
        if enabled:
            user.notification_cursor_at = datetime.now(timezone.utc)
            user.notification_started_at = user.notification_cursor_at
        else:
            await self._cancel_open_deliveries(telegram_id)
        await self._session.commit()
        return enabled

    async def deactivate(self, telegram_id: int) -> None:
        await self._users.set_active(telegram_id, False)
        await self._session.commit()

    async def _cancel_open_deliveries(self, telegram_id: int) -> None:
        await self._session.execute(
            update(ListingDelivery)
            .where(
                ListingDelivery.telegram_id == telegram_id,
                ListingDelivery.status.in_(("pending", "processing")),
            )
            .values(status="cancelled", lease_expires_at=None)
        )

    async def _locked_user(self, telegram_id):
        return await self._session.scalar(
            select(BotUser)
            .where(BotUser.telegram_id == telegram_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def reset_preferences(self, telegram_id):
        user = await self._locked_user(telegram_id)
        await self._session.execute(delete(UserPreference).where(UserPreference.telegram_id == telegram_id))
        if user:
            user.notification_cursor_at = datetime.now(timezone.utc)
            user.notification_started_at = user.notification_cursor_at
        await self._cancel_open_deliveries(telegram_id)
        await self._session.commit()

    async def get_profiles(self, telegram_id, *, enabled_only=False):
        query = select(UserPreference).where(UserPreference.telegram_id == telegram_id)
        if enabled_only:
            query = query.where(UserPreference.enabled.is_(True))
        return list((await self._session.scalars(query.order_by(UserPreference.id))).all())

    async def get_profile(self, telegram_id, profile_id):
        return await self._session.scalar(
            select(UserPreference)
            .where(UserPreference.telegram_id == telegram_id, UserPreference.id == profile_id)
            .execution_options(populate_existing=True)
        )

    async def save_profile(self, telegram_id, *, profile_id=None, name, **fields):
        user = await self._locked_user(telegram_id)
        if user is None:
            raise ValueError("Пользователь не найден")
        name = name.strip()
        if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
            raise ValueError("Название должно содержать от 1 до 40 символов без переносов строк")
        duplicate = await self._session.scalar(
            select(UserPreference.id).where(
                UserPreference.telegram_id == telegram_id,
                func.lower(UserPreference.name) == name.lower(),
                UserPreference.id != (profile_id or 0),
            )
        )
        if duplicate is not None:
            raise ValueError("Набор с таким названием уже есть")
        profile = await self.get_profile(telegram_id, profile_id) if profile_id else None
        if profile_id and profile is None:
            raise ValueError("Набор не найден")
        now = datetime.now(timezone.utc)
        if profile is None:
            profile = UserPreference(telegram_id=telegram_id, name=name, enabled=False)
            self._session.add(profile)
        profile.name = name
        for key in ["city", "rooms", "price_usd_min", "price_usd_max"]:
            setattr(profile, key, fields.get(key))
        profile.started_at = now
        profile.updated_at = now
        await self._session.commit()
        return profile

    async def set_profile_enabled(self, telegram_id, profile_id, enabled):
        user = await self._locked_user(telegram_id)
        profile = await self.get_profile(telegram_id, profile_id)
        if profile is None:
            raise ValueError("Набор не найден")
        if profile.enabled != enabled:
            profile.enabled = enabled
            if enabled:
                profile.started_at = datetime.now(timezone.utc)
                if user and user.notifications and user.notification_cursor_at is None:
                    user.notification_cursor_at = profile.started_at
                    user.notification_started_at = profile.started_at
        await self._session.commit()
        return profile

    async def delete_profile(self, telegram_id, profile_id):
        await self._locked_user(telegram_id)
        await self._session.execute(
            delete(UserPreference).where(UserPreference.telegram_id == telegram_id, UserPreference.id == profile_id)
        )
        await self._session.commit()
