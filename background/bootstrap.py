"""Seed operational configuration after ``alembic upgrade head``."""

from __future__ import annotations

import asyncio

from background.task_db import ensure_database


async def main() -> None:
    await ensure_database()


if __name__ == "__main__":
    asyncio.run(main())
