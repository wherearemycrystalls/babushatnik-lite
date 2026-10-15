import asyncio
import logging

from bot.app import BotApplication
from bot.config import BotConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> None:
    config = BotConfig.from_env()
    app = BotApplication(config)
    try:
        await app.start()
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
