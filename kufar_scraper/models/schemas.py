from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class CityConfig(BaseModel):
    name: str
    rgn: str
    ar: str


class KufarImage(BaseModel):
    path: str | None = None
    model_config = {"extra": "allow"}


class KufarParam(BaseModel):
    p: str
    v: Any = None
    pl: str | None = None
    vl: Any = None
    model_config = {"extra": "allow"}


class KufarAdRaw(BaseModel):
    ad_id: int
    list_time: str | None = None
    subject: str | None = None
    price_byn: str | int | None = None
    price_usd: str | int | None = None
    price_eur: str | int | None = None
    ad_parameters: list[KufarParam] = Field(default_factory=list)
    account_parameters: list[KufarParam] = Field(default_factory=list)
    images: list[KufarImage] = Field(default_factory=list)
    body: str | None = None
    body_short: str | None = None
    ad_link: str | None = None
    model_config = {"extra": "allow"}


class ParsedListing(BaseModel):
    ad_id: int
    city: str
    list_time: datetime | None = None
    title: str | None = None
    price_byn: Decimal | None = None
    price_usd: Decimal | None = None
    price_eur: Decimal | None = None
    currency: str | None = None
    account_id: int | None = None
    list_id: int | None = None
    company_ad: bool | None = None
    category: str | None = None
    ad_type: str | None = None
    remuneration_type: str | None = None
    address: str | None = None
    rooms: str | None = None
    floor: str | None = None
    total_floors: int | None = None
    year_built: int | None = None
    area_total: Decimal | None = None
    area_living: Decimal | None = None
    area_kitchen: Decimal | None = None
    longitude: Decimal | None = None
    latitude: Decimal | None = None
    distance_to_center_km: Decimal | None = None
    repair: str | None = None
    house_type: str | None = None
    bathroom: str | None = None
    balcony: str | None = None
    prepayment: str | None = None
    description: str | None = None
    link: str | None = None
    images: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    parameter_details: dict = Field(default_factory=dict)
    raw_search: dict = Field(default_factory=dict)
    raw_detail: dict | None = None
    source_observed_at: datetime | None = None
    details_fetched_at: datetime | None = None
    data_warnings: list[str] = Field(default_factory=list)
