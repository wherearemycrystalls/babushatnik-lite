from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import BotConfig
from bot.db.session import get_session_factory, init_engine
from bot.handlers import criteria, fallback, favourites, settings, start
from bot.services.listing_service import ListingService
from bot.services.user_service import UserService

logger = logging.getLogger(__name__)


class DependencyMiddleware(BaseMiddleware):
    """
    Injects per-request dependencies into handler data dict:
      - session         AsyncSession
      - user_service    UserService
      - listing_service ListingService
      - config          BotConfig
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config: BotConfig) -> None:
        self._session_factory = session_factory
        self._config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            data["user_service"] = UserService(session)
            data["listing_service"] = ListingService(session)
            data["config"] = self._config
            return await handler(event, data)


class BotApplication:
    """Owns the full lifecycle of the Telegram bot process."""

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None

    async def start(self) -> None:
        init_engine(self._config.database_url)

        session_factory = get_session_factory()

        self._bot = Bot(token=self._config.bot_token)
        await self._bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="criteria", description="Критерии поиска"),
                BotCommand(command="favourites", description="Избранное и архив"),
                BotCommand(command="settings", description="Приостановить или возобновить поиск"),
                BotCommand(command="help", description="Как работает поиск"),
            ]
        )
        storage = RedisStorage.from_url(self._config.redis_url)
        self._dp = Dispatcher(storage=storage)

        # Middleware (runs for every update)
        self._dp.update.outer_middleware(DependencyMiddleware(session_factory, self._config))

        # Routers — order matters for FSM state isolation
        self._dp.include_router(start.router)
        self._dp.include_router(criteria.router)
        self._dp.include_router(favourites.router)
        self._dp.include_router(settings.router)
        self._dp.include_router(fallback.router)

        logger.info("Bot started — polling...")
        await self._dp.start_polling(self._bot, allowed_updates=self._dp.resolve_used_update_types())

    async def stop(self) -> None:
        if self._bot:
            await self._bot.session.close()
        logger.info("Bot stopped.")
