from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from kufar_scraper.db.models import Listing


class BotBase(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BotUser(BotBase):
    __tablename__ = "bot_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notification_interval_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    notification_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_cursor_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    preferences: Mapped[list[UserPreference]] = relationship(back_populates="user", cascade="all, delete-orphan")
    favourites: Mapped[list[UserFavourite]] = relationship(back_populates="user", cascade="all, delete-orphan")
    notification_deliveries: Mapped[list[NotificationDelivery]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserPreference(BotBase):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("telegram_id", "name", name="uq_search_profile_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(40), default="Основной", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rooms: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)
    price_usd_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_usd_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[BotUser] = relationship(back_populates="preferences")


class UserFavourite(BotBase):
    __tablename__ = "user_favourites"
    __table_args__ = (UniqueConstraint("telegram_id", "ad_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Listing.ad_id, ondelete="CASCADE"), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[BotUser] = relationship(back_populates="favourites")


class UserSeen(BotBase):
    __tablename__ = "user_seen"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Listing.ad_id, ondelete="CASCADE"), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class NotificationDelivery(BotBase):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[BotUser] = relationship(back_populates="notification_deliveries")


class ListingDelivery(BotBase):
    __tablename__ = "listing_deliveries"
    __table_args__ = (UniqueConstraint("telegram_id", "ad_id", name="uq_listing_delivery_user_ad"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bot_users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Listing.ad_id, ondelete="CASCADE"), nullable=False)
    matched_profiles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
