from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CityConfigDB(Base):
    __tablename__ = "city_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rgn: Mapped[str] = mapped_column(String(20), nullable=False)
    ar: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    availability_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_initialized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Listing(Base):
    """A Kufar listing plus source and manual visibility state."""

    __tablename__ = "listings"

    ad_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    city: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    price_byn: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    price_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    rooms: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    floor: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    images: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    parameters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_source_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    list_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    first_list_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    details_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    content_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_eur: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    account_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    list_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    company_ad: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ad_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    remuneration_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    total_floors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    year_built: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    area_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    area_living: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    area_kitchen: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(11, 8), nullable=True)
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(11, 8), nullable=True)
    distance_to_center_km: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3), nullable=True)
    repair: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    house_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bathroom: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    balcony: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prepayment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameter_details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_search: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    data_warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_inactive_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_hidden_by_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    manual_archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSchedule(Base):
    __tablename__ = "task_schedules"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_enqueued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False, index=True)
    mode: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    city_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    counters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ListingChange(Base):
    __tablename__ = "listing_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.ad_id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False)
