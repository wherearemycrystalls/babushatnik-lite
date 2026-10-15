import asyncio
import logging

import config
from kufar_scraper.db.client import (
    get_active_cities,
    get_session,
    init_engine,
    seed_default_cities,
    upsert_listing,
)
from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.client import KufarClient
from kufar_scraper.scraper.parser import parse_ad

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def scrape_city(city: CityConfig) -> int:
    """Scrapes one city and saves results to DB. Returns number of upserted listings."""
    saved = 0
    async with KufarClient() as client:
        fetched = await client.fetch_all_ads(city)
    if not fetched.completed:
        raise RuntimeError(f"Full scrape incomplete: {fetched.error}")

    async with get_session() as session:
        for raw in fetched.ads:
            try:
                listing = parse_ad(raw)
                await upsert_listing(session, listing)
                saved += 1
            except Exception as exc:
                logger.warning("Failed to process listing %s: %s", raw.get("ad_id"), exc)

    return saved


async def run_once() -> None:
    """Single pass over all active cities from DB."""
    async with get_session() as session:
        cities = await get_active_cities(session)

    if not cities:
        logger.warning("No active cities in city_configs — skipping run.")
        return

    logger.info("Starting parse run. Active cities: %d", len(cities))
    for city in cities:
        try:
            count = await scrape_city(city)
            logger.info("[%s] Saved/updated: %d", city.name, count)
        except Exception as exc:
            logger.error("[%s] Critical error: %s", city.name, exc)


async def main() -> None:
    init_engine(config.DATABASE_URL)

    # Schema is created by Alembic; this only ensures configured cities exist.
    async with get_session() as session:
        await seed_default_cities(session, config.CITIES)

    logger.info("Scraper module ready. Parsing and its interval are managed by database schedules and Celery.")
    # Keep the process alive without auto-running a parse
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
