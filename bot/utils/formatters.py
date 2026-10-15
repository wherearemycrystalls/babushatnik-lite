from __future__ import annotations

import re

from bot.db.models import UserPreference
from bot.utils.listing_info import number, parameter_value, source_time
from kufar_scraper.db.models import Listing

_ANY = "Любой"


def _escape(value, limit=200):
    text = str(value or "")[:limit]
    return re.sub(r"([\\_*`\[])", r"\\\1", text)


def _link(listing):
    if not listing.link:
        return ""
    url = listing.link.replace("(", "%28").replace(")", "%29")
    if len(url) > 200:
        url = f"https://re.kufar.by/vi/{listing.ad_id}"
    return f"\n\n[Открыть на Kufar]({url})"


def _facts(listing):
    facts = []
    if listing.area_total is not None:
        facts.append(f"Площадь: {number(listing.area_total)} м²")
    if listing.floor:
        facts.append(f"Этаж: {listing.floor}" + (f"/{listing.total_floors}" if listing.total_floors else ""))
    if listing.repair:
        facts.append("Ремонт: " + listing.repair)
    if listing.company_ad is not None:
        facts.append("Объявление компании" if listing.company_ad else "Частное объявление")
    facts.append("Дата в Kufar: " + source_time(listing.list_time))
    return facts


def format_card(listing: Listing) -> str:
    rooms = listing.rooms or "?"
    city = listing.city.capitalize() if listing.city else "?"
    price = f"${number(listing.price_usd)}/мес." if listing.price_usd is not None else "не указана"
    lines = [
        f"*{_escape(rooms, 10)}-комн. квартира · {_escape(city, 40)}*",
        "Адрес: " + _escape(listing.address or "не указан", 120),
        "Цена: " + price,
        "Дата в Kufar: " + source_time(listing.list_time),
    ]
    tail = _link(listing)
    optional = ([_escape(listing.title, 60)] if listing.title else []) + [
        _escape(fact, 72) for fact in _facts(listing)[:-1]
    ]
    for line in optional:
        if len("\n".join(lines + [line]) + tail) <= 1000:
            lines.append(line)
    return "\n".join(lines) + tail


def format_full_detail(listing: Listing) -> str:
    city = listing.city.capitalize() if listing.city else "?"
    prices = [
        f"${number(listing.price_usd)}" if listing.price_usd is not None else "",
        f"{number(listing.price_byn)} BYN" if listing.price_byn is not None else "",
    ]
    lines = [f"🏠 *{_escape(listing.rooms or '?', 10)}-комн. квартира* · {_escape(city, 50)}"]
    if listing.title:
        lines.append(_escape(listing.title, 120))
    lines += [
        "📍 " + _escape(listing.address or "адрес не указан", 250),
        "💰 " + (" / ".join(filter(None, prices)) or "цена не указана"),
    ]
    lines += [_escape(fact, 120) for fact in _facts(listing)]
    if listing.year_built or listing.house_type:
        lines.append(_escape("Дом: " + ", ".join(str(v) for v in [listing.house_type, listing.year_built] if v), 140))
    details = (listing.parameter_details or {}).get("ad", {})
    for key in ["flat_improvement", "flat_bath", "flat_kitchen", "flat_rent_prepayment"]:
        if key in details:
            entry = details[key]
            lines.append(_escape(f"{entry.get('pl') or key}: {parameter_value(entry)}", 140))
    tail = _link(listing)
    # Keep every Markdown escape intact and stay below Telegram's 4096-character limit.
    kept = []
    for line in lines:
        if len("\n".join(kept + [line]) + tail) <= 2100:
            kept.append(line)
    budget = max(0, (3800 - len("\n".join(kept) + tail) - 2) // 2)
    kept += ["", _escape(listing.description or "Описание не указано", min(800, budget))]
    return "\n".join(kept) + tail


def format_criteria_summary(prefs: UserPreference) -> str:
    city = prefs.city.capitalize() if prefs.city else _ANY
    rooms = ", ".join(prefs.rooms) + "-комн." if prefs.rooms else _ANY
    price_min = f"${prefs.price_usd_min}" if prefs.price_usd_min else "—"
    price_max = f"${prefs.price_usd_max}" if prefs.price_usd_max else "—"
    return f"*Ваши критерии поиска:*\n🏙 Город: {_escape(city)}\n🏠 Комнат: {_escape(rooms)}\n💰 Цена: {price_min} – {price_max}"
