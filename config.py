import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

from kufar_scraper.models.schemas import CityConfig

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_database_url() -> str:
    has_pg_env = any(
        os.getenv(key)
        for key in (
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
        )
    )

    if not has_pg_env:
        url = os.getenv("DATABASE_URL")
        if url:
            return url

    user = os.getenv("POSTGRES_USER", "kufar")
    password = os.getenv("POSTGRES_PASSWORD", "kufar")
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "kufar")
    user_escaped = quote_plus(user)
    password_escaped = quote_plus(password)
    return f"postgresql+asyncpg://{user_escaped}:{password_escaped}@{host}:{port}/{db_name}"


DATABASE_URL: str = _build_database_url()
# Poll frequently; individual matches are delivered without a user-level digest interval.
NEW_LISTINGS_DEFAULT_INTERVAL_SECONDS: int = int(os.getenv("NEW_LISTINGS_INTERVAL_SECONDS", "60"))
NEW_LISTINGS_MAX_PAGES: int = int(os.getenv("NEW_LISTINGS_MAX_PAGES", "5"))
FULL_RECONCILIATION_INTERVAL_SECONDS: int = int(os.getenv("FULL_RECONCILIATION_INTERVAL_SECONDS", str(24 * 60 * 60)))
FULL_RECONCILIATION_MAX_PAGES: int = int(os.getenv("FULL_RECONCILIATION_MAX_PAGES", "0"))
NOTIFICATION_SCAN_INTERVAL_SECONDS: int = int(os.getenv("NOTIFICATION_SCAN_INTERVAL", "10"))

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

AUTH_BOT_TOKEN: str = os.getenv("AUTH_BOT_TOKEN", "")
SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "change-me-in-production")
ADMIN_TELEGRAM_AUTH_ENABLED: bool = _env_bool("ADMIN_TELEGRAM_AUTH_ENABLED", True)

# Telegram user IDs allowed to access admin. Comma-separated, e.g. "123456789,987654321"
ALLOWED_TELEGRAM_IDS: set[int] = {
    int(x.strip()) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()
}


def _parse_cities() -> list[CityConfig]:
    raw = os.getenv("CITIES", "grodno:3:9")
    cities = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            cities.append(CityConfig(name=parts[0], rgn=parts[1], ar=parts[2]))
    if not cities:
        raise ValueError("CITIES env var is empty or malformed. Expected: name:rgn:ar,...")
    return cities


CITIES: list[CityConfig] = _parse_cities()

CITY_CENTERS = {"grodno": (53.677755, 23.830071)}

NEW_LISTINGS_KNOWN_PAGE_OVERLAP: int = int(os.getenv("NEW_LISTINGS_KNOWN_PAGE_OVERLAP", "2"))
DISPLAY_TIMEZONE: str = os.getenv("DISPLAY_TIMEZONE", "Europe/Minsk")

AVAILABILITY_CHECK_INTERVAL_SECONDS: int = int(os.getenv("AVAILABILITY_CHECK_INTERVAL_SECONDS", "300"))
