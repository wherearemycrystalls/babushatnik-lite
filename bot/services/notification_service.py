from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import BotUser, ListingDelivery, UserPreference
from bot.services.listing_service import _build_filter_clauses, visible_listing_clause
from bot.utils.feed import send_listing
from kufar_scraper.db.client import lock_catalog_for_notifications
from kufar_scraper.db.models import Listing

logger = logging.getLogger(__name__)
OPEN_DELIVERY_STATUSES = ("pending", "processing")
LEASE_DURATION = timedelta(minutes=5)
DELIVERY_BATCH_SIZE = 25


class NotificationService:
    """One durable outbox entry per user/apartment; source bumps never resend an ID."""

    def __init__(self, session: AsyncSession, bot: Bot) -> None:
        self._session = session
        self._bot = bot

    async def run_cycle(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        return {**await self.schedule_due_deliveries(now), **await self.deliver_pending(now)}

    def _matching_clauses(self, prefs, since, until, source_since):
        return [
            visible_listing_clause(),
            Listing.created_at > since,
            Listing.created_at <= until,
            Listing.list_time >= source_since,
            Listing.list_time <= until + timedelta(minutes=5),
            *([Listing.created_at > prefs.started_at] if prefs.started_at else []),
            *_build_filter_clauses(prefs),
        ]

    def _profile_since(self, profile, user, fallback):
        return max(
            value
            for value in [
                _as_utc(profile.started_at),
                _as_utc(user.notification_started_at),
                fallback if not user.notification_started_at and not profile.started_at else None,
            ]
            if value is not None
        )

    async def _matching_profiles(self, listing, user):
        if listing is None or not listing.is_source_active or listing.is_hidden_by_admin:
            return []
        profiles = list(
            (
                await self._session.scalars(
                    select(UserPreference)
                    .where(UserPreference.telegram_id == user.telegram_id, UserPreference.enabled.is_(True))
                    .order_by(UserPreference.id)
                )
            ).all()
        )
        matches = []
        for profile in profiles:
            since = self._profile_since(
                profile, user, _as_utc(user.notification_cursor_at) or datetime.now(timezone.utc)
            )
            clauses = [Listing.ad_id == listing.ad_id, Listing.list_time >= since, *_build_filter_clauses(profile)]
            if profile.started_at:
                clauses.append(Listing.created_at > profile.started_at)
            if await self._session.scalar(select(Listing.ad_id).where(*clauses)) is not None:
                matches.append({"id": profile.id, "name": profile.name})
        return matches

    async def schedule_due_deliveries(self, now: datetime) -> dict[str, int]:
        counters = {"users_checked": 0, "deliveries_created": 0, "cursor_advanced": 0}
        ids = list(
            (
                await self._session.scalars(
                    select(BotUser.telegram_id).where(BotUser.is_active.is_(True), BotUser.notifications.is_(True))
                )
            ).all()
        )
        await self._session.commit()
        for telegram_id in ids:
            user = await self._session.scalar(
                select(BotUser)
                .where(BotUser.telegram_id == telegram_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if user is None or not user.is_active or not user.notifications:
                await self._session.commit()
                continue
            counters["users_checked"] += 1
            profiles = list(
                (
                    await self._session.scalars(
                        select(UserPreference)
                        .where(UserPreference.telegram_id == telegram_id, UserPreference.enabled.is_(True))
                        .order_by(UserPreference.id)
                    )
                ).all()
            )
            cursor = _as_utc(user.notification_cursor_at)
            if profiles and cursor is not None:
                await lock_catalog_for_notifications(self._session)
                matches = {}
                for profile in profiles:
                    source_since = self._profile_since(profile, user, cursor)
                    rows = (
                        await self._session.execute(
                            select(Listing.ad_id, Listing.list_time).where(
                                *self._matching_clauses(profile, cursor, now, source_since)
                            )
                        )
                    ).all()
                    for ad_id, list_time in rows:
                        item = matches.setdefault(ad_id, {"list_time": list_time, "profiles": []})
                        item["profiles"].append({"id": profile.id, "name": profile.name})
                for ad_id, match in sorted(matches.items(), key=lambda pair: (pair[1]["list_time"], pair[0])):
                    row = await self._session.scalar(
                        insert(ListingDelivery)
                        .values(
                            telegram_id=telegram_id,
                            ad_id=ad_id,
                            matched_profiles=match["profiles"],
                            status="pending",
                            attempts=0,
                            next_attempt_at=now,
                            created_at=now,
                        )
                        .on_conflict_do_nothing(constraint="uq_listing_delivery_user_ad")
                        .returning(ListingDelivery.id)
                    )
                    counters["deliveries_created"] += int(row is not None)
                user.notification_started_at = _as_utc(user.notification_started_at) or cursor
            elif user.notification_started_at is None:
                user.notification_started_at = now
            # Delivery retry is independent of matching; never stall a cursor on an old failed card.
            user.notification_cursor_at = max(cursor or now, now)
            await self._session.commit()
            counters["cursor_advanced"] += 1
        return counters

    async def _new_by_criteria_count(self, telegram_id, prefs, since, until, source_since=None):
        await lock_catalog_for_notifications(self._session)
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(*self._matching_clauses(prefs, since, until, source_since or since))
            )
            or 0
        )

    async def cancel_open_deliveries(self, telegram_id: int) -> None:
        await self._session.execute(
            update(ListingDelivery)
            .where(ListingDelivery.telegram_id == telegram_id, ListingDelivery.status.in_(OPEN_DELIVERY_STATUSES))
            .values(status="cancelled", lease_expires_at=None)
        )

    async def deliver_pending(self, now: datetime) -> dict[str, int]:
        counters = {"deliveries_claimed": 0, "cards_sent": 0, "retry_scheduled": 0, "disabled_users": 0, "cancelled": 0}
        await self._session.execute(
            update(ListingDelivery)
            .where(ListingDelivery.status == "processing", ListingDelivery.lease_expires_at <= now)
            .values(status="pending", lease_expires_at=None, last_error="delivery lease expired; retrying")
        )
        await self._session.commit()
        # Claim only the next card immediately before sending; a queue cannot outlive its leases.
        for _ in range(DELIVERY_BATCH_SIZE):
            delivery = await self._session.scalar(
                select(ListingDelivery)
                .where(ListingDelivery.status == "pending", ListingDelivery.next_attempt_at <= now)
                .order_by(ListingDelivery.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if delivery is None:
                await self._session.commit()
                break
            delivery.status = "processing"
            delivery.attempts += 1
            delivery.lease_expires_at = datetime.now(timezone.utc) + LEASE_DURATION
            delivery_id = delivery.id
            await self._session.commit()
            counters["deliveries_claimed"] += 1
            result = await self._deliver_one(delivery_id)
            counters[result] += 1
            if result == "cards_sent":
                await asyncio.sleep(1)
        return counters

    async def _deliver_one(self, delivery_id: int) -> str:
        # User lock serializes sending with pause/reset/criteria changes.
        delivery = await self._session.get(ListingDelivery, delivery_id)
        if delivery is None or delivery.status != "processing":
            return "cancelled"
        user = await self._session.scalar(
            select(BotUser)
            .where(BotUser.telegram_id == delivery.telegram_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self._session.refresh(delivery)
        listing = await self._session.get(Listing, delivery.ad_id, populate_existing=True)
        matches = await self._matching_profiles(listing, user) if user else []
        if delivery.status != "processing" or not user or not user.is_active or not user.notifications or not matches:
            delivery.status = "cancelled"
            delivery.lease_expires_at = None
            await self._session.commit()
            return "cancelled"
        try:
            delivery.matched_profiles = matches
            message = await send_listing(self._bot, delivery.telegram_id, listing, matched_profiles=matches)
        except TelegramForbiddenError:
            user.is_active = False
            await self.cancel_open_deliveries(user.telegram_id)
            await self._session.commit()
            return "disabled_users"
        except TelegramRetryAfter as exc:
            await self._reschedule_retry(delivery, max(1, int(exc.retry_after)), "Telegram rate limit", defer_user=True)
            return "retry_scheduled"
        except Exception as exc:
            logger.warning("Apartment delivery %s failed: %s", delivery_id, type(exc).__name__)
            await self._reschedule_retry(delivery, min(300, 2 ** min(delivery.attempts, 8)), str(exc))
            return "retry_scheduled"
        delivery.status = "delivered"
        delivery.message_id = getattr(message, "message_id", None)
        delivery.delivered_at = datetime.now(timezone.utc)
        delivery.lease_expires_at = None
        delivery.last_error = None
        await self._session.commit()
        return "cards_sent"

    async def _reschedule_retry(self, delivery, delay_seconds, error, *, defer_user=False):
        delivery.status = "pending"
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        delivery.lease_expires_at = None
        delivery.last_error = error[:2000]
        if defer_user:
            await self._session.execute(
                update(ListingDelivery)
                .where(
                    ListingDelivery.telegram_id == delivery.telegram_id,
                    ListingDelivery.status == "pending",
                    ListingDelivery.next_attempt_at < delivery.next_attempt_at,
                )
                .values(next_attempt_at=delivery.next_attempt_at)
            )
        await self._session.commit()


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
