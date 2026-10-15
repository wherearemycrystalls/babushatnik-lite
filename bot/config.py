from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class BotConfig:
    bot_token: str
    database_url: str
    redis_url: str = "redis://localhost:6379/1"
    max_photos_per_card: int = 5
    max_favourites: int = 200

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            bot_token=_get_bot_token(),
            database_url=_build_database_url(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/1"),
        )


def _get_bot_token() -> str:
    token = os.getenv("LITE_BOT_TOKEN") or os.getenv("FINDER_BOT_TOKEN")
    if not token:
        raise KeyError("LITE_BOT_TOKEN")
    return token


def _build_database_url() -> str:
    """Mirrors the logic in config.py at the project root."""
    if not any(
        os.getenv(k) for k in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT")
    ):
        url = os.getenv("DATABASE_URL")
        if url:
            return url

    user = quote_plus(os.getenv("POSTGRES_USER", "kufar"))
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", "kufar"))
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "kufar")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"
