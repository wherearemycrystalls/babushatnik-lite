import hashlib
import hmac
import time

from fastapi import Request

import config

SESSION_KEY = "tg_user"


def verify_telegram_data(data: dict) -> bool:
    """Verify authenticity of data from Telegram Login Widget."""
    if "hash" not in data:
        return False

    data = dict(data)
    received_hash = data.pop("hash")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))

    secret_key = hashlib.sha256(config.AUTH_BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return False

    # Data must not be older than 24 hours
    if (int(time.time()) - int(data.get("auth_date", 0))) > 86400:
        return False

    return True


def get_current_user(request: Request) -> dict | None:
    return request.session.get(SESSION_KEY)


class RequireAuth:
    """Dependency that redirects to login if user is not authenticated."""

    async def __call__(self, request: Request):
        if not config.ADMIN_TELEGRAM_AUTH_ENABLED:
            return {"id": "disabled", "username": "disabled"}
        user = get_current_user(request)
        if user is None:
            raise _AuthRedirect(request.url.path)
        if config.ALLOWED_TELEGRAM_IDS and int(user["id"]) not in config.ALLOWED_TELEGRAM_IDS:
            raise _AuthRedirect(request.url.path, forbidden=True)
        return user


class _AuthRedirect(Exception):
    def __init__(self, path: str, forbidden: bool = False):
        self.path = path
        self.forbidden = forbidden


require_auth = RequireAuth()
