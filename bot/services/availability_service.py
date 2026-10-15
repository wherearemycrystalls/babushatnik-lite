from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kufar_scraper.db.client import archive_unseen_from_completed_reconciliation, upsert_listing
from kufar_scraper.db.models import CityConfigDB, Listing
from kufar_scraper.models.schemas import CityConfig
from kufar_scraper.scraper.client import KufarClient
from kufar_scraper.scraper.parser import parse_ad


class AvailabilityService:
    def __init__(self, sessions):
        self._sessions = sessions

    async def check_city(self, name: str, *, max_age_seconds=60) -> str:
        async with self._sessions() as session:
            city = await session.scalar(
                select(CityConfigDB).where(CityConfigDB.name == name, CityConfigDB.enabled.is_(True))
            )
            if city is None:
                return "unavailable"
            if not await session.scalar(select(func.pg_try_advisory_xact_lock(540650000000 + city.id))):
                return "running"
            now = datetime.now(timezone.utc)
            if city.availability_checked_at and city.availability_checked_at > now - timedelta(seconds=max_age_seconds):
                return "cached"
            async with KufarClient() as client:
                fetched = await client.fetch_all_ads(
                    CityConfig(name=city.name, rgn=city.rgn, ar=city.ar), fetch_details=False
                )
            if not fetched.completed:
                return "unavailable"
            existing = set((await session.scalars(select(Listing.ad_id).where(Listing.city == name))).all())
            # Only a complete search can establish absence. Detail HTTP failures are never a removal signal.
            for raw in fetched.ads:
                if raw["ad_id"] in existing:
                    await upsert_listing(session, parse_ad(raw), commit=False)
            await archive_unseen_from_completed_reconciliation(session, name, now)
            city.availability_checked_at = datetime.now(timezone.utc)
            await session.commit()
            return "checked"
