import hashlib
import hmac
import os
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get("ENV_FILE", ".env"), extra="ignore", populate_by_name=True)

    # Telegram bot token from @BotFather (the backend needs the same one)
    bot_token: str = Field("", validation_alias=AliasChoices("BOT_TOKEN", "TOKEN"))
    # Where the backend listens, as the bot reaches it
    backend_url: str = "http://localhost:8000"
    # The key for the backend's /internal API. Empty: derived from the bot token, which both have
    internal_api_key: str = ""
    # Where the bot keeps conversation steps between restarts; empty keeps them in memory only
    bot_state_file: str = "./data/bot-state.pickle"

    # Public HTTPS address Telegram sends updates to, e.g. https://pay.example.com/telegram. Empty: the bot
    # asks Telegram for updates itself (long polling), which needs no public address
    webhook_url: str = ""
    # Where the webhook server listens inside the server; the reverse proxy forwards WEBHOOK_URL here
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8081
    # Telegram sends this in X-Telegram-Bot-Api-Secret-Token, so only Telegram can post updates.
    # Empty: derived from the bot token
    webhook_secret: str = ""

    @property
    def api_key(self) -> str:
        if self.internal_api_key:
            return self.internal_api_key
        return hmac.new(self.bot_token.encode(), b"internal-api", hashlib.sha256).hexdigest()

    @property
    def webhook_secret_token(self) -> str:
        """1-256 characters of A-Z, a-z, 0-9, _ and - (Telegram's rule): a hex digest fits."""
        return self.webhook_secret or hmac.new(self.bot_token.encode(), b"webhook", hashlib.sha256).hexdigest()

    @property
    def webhook_path(self) -> str:
        """The URL path the webhook server answers on: the path of WEBHOOK_URL."""
        return urlparse(self.webhook_url).path.strip("/") or "telegram"


@lru_cache
def get_settings() -> BotSettings:
    return BotSettings()
