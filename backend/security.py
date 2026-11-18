"""Who is calling.

The Web App proves who the user is with the signed `initData` Telegram hands it (APP-1, NFR-1):
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
The bot proves itself with a shared key.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


@dataclass
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


def validate_init_data(init_data: str, bot_token: str, max_age: int) -> TelegramUser:
    """The Telegram user the data was signed for. Refused when the signature is wrong or the data is too old."""
    if not init_data or not bot_token:
        raise InitDataError("Open this app from the Telegram bot")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received = pairs.pop("hash", None)
    if not received:
        raise InitDataError("Open this app from the Telegram bot")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("Open this app from the Telegram bot")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise InitDataError("Open this app from the Telegram bot")
    if max_age and time.time() - auth_date > max_age:
        raise InitDataError("This session has expired. Close the app and open it again from the bot.")
    try:
        user = json.loads(pairs["user"])
        return TelegramUser(
            id=int(user["id"]),
            first_name=user.get("first_name", ""),
            last_name=user.get("last_name"),
            username=user.get("username"),
            language_code=user.get("language_code"),
        )
    except (KeyError, ValueError, TypeError):
        raise InitDataError("Open this app from the Telegram bot")


def same_key(given: str | None, expected: str) -> bool:
    return bool(given) and bool(expected) and hmac.compare_digest(given.encode(), expected.encode())
