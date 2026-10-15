from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import config


def number(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return format(value, "f").rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else str(value)
    return str(value)


def source_time(value: datetime | None) -> str:
    if value is None or value.tzinfo is None:
        return "не указано"
    return value.astimezone(ZoneInfo(config.DISPLAY_TIMEZONE)).strftime("%d.%m.%Y %H:%M")


def parameter_value(entry: dict):
    value = entry.get("vl")
    if value is None or value == "" or (isinstance(value, list) and not any(str(item) for item in value)):
        value = entry.get("v")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "—" if value is None else str(value)
