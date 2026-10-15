"""Run Alembic and bridge databases created by the pre-Alembic runtime DDL."""

from __future__ import annotations

import asyncio
import subprocess

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config

LEGACY_HEAD = "20260810_0003"


async def stamp_legacy_schema_if_needed() -> None:
    """Stamp the known pre-refactor runtime schema before applying 0004."""
    engine = create_async_engine(config.DATABASE_URL)
    try:
        async with engine.begin() as connection:
            listings_exists = await connection.scalar(text("SELECT to_regclass('public.listings') IS NOT NULL"))
            version_table_exists = await connection.scalar(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            )
            version = (
                await connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
                if version_table_exists
                else None
            )
            if listings_exists and version is None:
                if not version_table_exists:
                    await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
                await connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": LEGACY_HEAD},
                )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(stamp_legacy_schema_if_needed())
    subprocess.run(["alembic", "upgrade", "head"], check=True)


if __name__ == "__main__":
    main()
