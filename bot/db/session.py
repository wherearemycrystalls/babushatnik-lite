from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_database_url: str | None = None


def init_engine(database_url: str) -> None:
    global _engine, _session_factory, _database_url
    if _engine is not None and _database_url == database_url:
        return
    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=3,
        max_overflow=5,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    _database_url = database_url


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _session_factory
