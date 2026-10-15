from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from kufar_scraper.models.schemas import ParsedListing

# These fields drive detail refresh; request/tracking IDs do not describe an apartment.
SEARCH_CONTENT_FIELDS = (
    "list_time",
    "subject",
    "price_byn",
    "price_usd",
    "price_eur",
    "currency",
    "company_ad",
    "category",
    "type",
    "remuneration_type",
    "body",
    "body_short",
    "images",
    "ad_parameters",
    "account_parameters",
    "ad_link",
)
PARAMETER_COLUMNS = {
    "address": "address",
    "rooms": "rooms",
    "floor": "floor",
    "total_floors": "re_number_floors",
    "year_built": "year_built",
    "area_total": "size",
    "area_living": "size_living_space",
    "area_kitchen": "size_kitchen",
    "longitude": "coordinates",
    "latitude": "coordinates",
    "distance_to_center_km": "distance_to_center_km",
    "repair": "flat_repair",
    "house_type": "house_type",
    "bathroom": "bathroom",
    "balcony": "balcony",
    "prepayment": "flat_rent_prepayment",
}


def parse_source_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def search_content(raw: dict) -> dict:
    return {key: raw.get(key) for key in SEARCH_CONTENT_FIELDS}


def parse_ad(raw: dict) -> ParsedListing:
    search = deepcopy(raw.get("_raw_search") or {k: v for k, v in raw.items() if not k.startswith("_")})
    response = raw.get("_detail_response")
    detail = response.get("result", response) if isinstance(response, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    values = {**search, **detail}
    observed_at = parse_source_time(raw.get("_observed_at")) or datetime.now(timezone.utc)
    warnings = []
    source_time = parse_source_time(detail.get("list_time")) or parse_source_time(search.get("list_time"))
    if source_time is None:
        warnings.append("missing_or_invalid_list_time")
    elif source_time > observed_at + timedelta(minutes=5):
        warnings.append("list_time_in_future")
        source_time = None
    if raw.get("_detail_failed"):
        warnings.append("detail_unavailable")
    details: dict[str, dict] = {"ad": {}, "account": {}}
    for scope, field in [("ad", "ad_parameters"), ("account", "account_parameters")]:
        for payload in [search, detail]:
            for item in payload.get(field) or []:
                if isinstance(item, dict) and isinstance(item.get("p"), str):
                    key = item["p"]
                    details[scope][key] = {**details[scope].get(key, {}), **deepcopy(item)}
    params = {key: item.get("v") for scope in [details["account"], details["ad"]] for key, item in scope.items()}
    coordinates = params.get("coordinates")
    coordinates = coordinates if isinstance(coordinates, list) and len(coordinates) == 2 else [None, None]
    longitude = _number(coordinates[0], minimum=Decimal("-180"), maximum=Decimal("180"))
    latitude = _number(coordinates[1], minimum=Decimal("-90"), maximum=Decimal("90"))
    longitude = longitude.quantize(Decimal("0.00000001")) if longitude is not None else None
    latitude = latitude.quantize(Decimal("0.00000001")) if latitude is not None else None

    def label(key: str) -> str | None:
        entry = details["ad"].get(key, details["account"].get(key, {}))
        value = entry.get("vl")
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if v is not None and str(v)) or None
        return str(value) if value is not None and str(value) else None

    calculator = {
        item.get("currency"): item.get("price") for item in values.get("calculator") or [] if isinstance(item, dict)
    }

    def price(key, currency):
        return _parse_price(values.get(key) if values.get(key) is not None else calculator.get(currency))

    images = [
        f"https://rms3.kufar.by/v1/gallery/{image['path']}"
        for image in values.get("images") or []
        if isinstance(image, dict) and image.get("path")
    ]
    body = detail.get("body") or search.get("body") or raw.get("_full_body") or search.get("body_short")
    return ParsedListing(
        ad_id=raw["ad_id"],
        city=raw.get("_city", "unknown"),
        list_time=source_time,
        title=values.get("subject"),
        price_byn=price("price_byn", "BYN"),
        price_usd=price("price_usd", "USD"),
        price_eur=price("price_eur", "EUR"),
        currency=values.get("currency"),
        account_id=_integer(values.get("account_id")) or None,
        list_id=_integer(values.get("list_id")),
        company_ad=values.get("company_ad"),
        category=str(values["category"]) if values.get("category") is not None else None,
        ad_type=values.get("type"),
        remuneration_type=str(values["remuneration_type"]) if values.get("remuneration_type") is not None else None,
        address=params.get("address"),
        rooms=_string(params.get("rooms")),
        floor=_string(params.get("floor")),
        total_floors=_integer(params.get("re_number_floors")),
        year_built=_integer(params.get("year_built")),
        area_total=_scaled_number(params.get("size"), "0.01"),
        area_living=_scaled_number(params.get("size_living_space"), "0.01"),
        area_kitchen=_scaled_number(params.get("size_kitchen"), "0.01"),
        longitude=longitude,
        latitude=latitude,
        distance_to_center_km=_scaled_number(params.get("distance_to_center_km"), "0.001"),
        repair=label("flat_repair"),
        house_type=label("house_type"),
        bathroom=label("bathroom"),
        balcony=label("balcony"),
        prepayment=label("flat_rent_prepayment"),
        description=_clean_text(body),
        link=values.get("ad_link"),
        images=images,
        parameters=params,
        parameter_details=details,
        raw_search=search,
        raw_detail=deepcopy(response) if detail else None,
        source_observed_at=observed_at,
        details_fetched_at=parse_source_time(raw.get("_detail_fetched_at")) if detail else None,
        data_warnings=warnings,
    )


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else None if isinstance(value, list) else value


def _number(value: Any, minimum: Decimal = Decimal("0"), maximum: Decimal | None = None) -> Decimal | None:
    value = _first(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= minimum and (maximum is None or number <= maximum) else None
    except InvalidOperation:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number == number.to_integral_value() else None


def _scaled_number(value: Any, precision: str) -> Decimal | None:
    number = _number(value)
    return number.quantize(Decimal(precision)) if number is not None else None


def _string(value: Any) -> str | None:
    value = _first(value)
    return str(value) if value is not None else None


def _parse_price(raw: Any) -> Decimal | None:
    number = _number(raw)
    return (number / 100).quantize(Decimal("0.01")) if number is not None and number > 0 else None


def _clean_text(text: str | None) -> str | None:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() or None if isinstance(text, str) else None
