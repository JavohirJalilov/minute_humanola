import hashlib
import hmac
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class Settings(BaseSettings):
    # ENV_FILE names another configuration file (the tests point it at nothing)
    model_config = SettingsConfigDict(env_file=os.environ.get("ENV_FILE", ".env"), extra="ignore", populate_by_name=True)

    # Telegram bot token from @BotFather
    bot_token: str = Field("", validation_alias=AliasChoices("BOT_TOKEN", "TOKEN"))
    # Public HTTPS base URL of this service, e.g. https://pay.example.com
    base_url: str = "http://localhost:8000"
    # The key the bot shows to the backend's /internal API. Empty: derived from the bot token, which both have
    internal_api_key: str = ""
    # Daily database backup at this local time (HH:MM) into BACKUP_DIR, kept BACKUP_KEEP_DAYS days; empty turns it off
    backup_time: str = "03:30"
    backup_dir: str = "./data/backups"
    backup_keep_days: int = 30

    # The SQLite file. Only the backend opens it
    database_url: str = "sqlite+aiosqlite:///./data/doppi.db"
    # A "day" (recording day, today, date paid) is a calendar day in this timezone
    timezone: str = "Asia/Tashkent"

    # Telegram user ids of Super Admins, comma separated. The role exists only here (ROLE-4)
    super_admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Where "Contact us" leads, e.g. for an invite code: a Telegram @username or a link. Empty: the first
    # Super Admin (then Admin) with a Telegram username
    support_contact: str = ""
    # Max age of Telegram Web App initData in seconds
    init_data_max_age: int = 86400
    # Developer-only login with the X-Debug-User-Id header. Works only when BASE_URL is a localhost address,
    # and the app refuses to start with it on any other BASE_URL (NFR-6)
    debug_auth: bool = False
    # Background jobs (UseMinute sync, reminders, backup) run in this process. Every backend process may run
    # them: the database makes sure each job runs once
    run_jobs: bool = True

    # UseMinute partner-portal login: the browser login's web key, refresh token and organisation id.
    # The same values the Camera Assignment Bot calls MINUTE_*. Empty turns the sync off
    doppi_api_key: str = Field("", validation_alias=AliasChoices("DOPPI_API_KEY", "MINUTE_API_KEY"))
    doppi_refresh_token: str = Field("", validation_alias=AliasChoices("DOPPI_REFRESH_TOKEN", "MINUTE_REFRESH_TOKEN"))
    doppi_org: str = Field("", validation_alias=AliasChoices("DOPPI_ORG", "MINUTE_ORG"))
    doppi_api_url: str = "https://minute-api-production.nicebush-c949d8c8.eastus2.azurecontainerapps.io/api/v1"
    doppi_token_url: str = "https://securetoken.googleapis.com/v1/token"
    doppi_referer: str = "https://useminute.app/"
    # Local times (HH:MM, comma separated) of the scheduled sync; empty turns the schedule off
    doppi_sync_times: str = Field("00:00", validation_alias=AliasChoices("DOPPI_SYNC_TIMES", "DOPPI_SYNC_TIME", "MINUTE_SYNC_TIME"))
    # Every sync re-reads this many days of uploads, because UseMinute keeps reviewing older recordings
    doppi_sync_days: int = Field(30, ge=30, validation_alias=AliasChoices("DOPPI_SYNC_DAYS", "MINUTE_SYNC_DAYS"))
    # The very first sync reads this many days, to bring in the history
    doppi_initial_days: int = Field(365, ge=30)

    # Email sender for verification codes (EXT-13): SMTP or a transactional provider's SMTP endpoint
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    # starttls (port 587), ssl (port 465) or none
    smtp_security: str = "starttls"
    # Address on the company's domain the codes come from; defaults to SMTP_USER
    smtp_from: str = ""

    @field_validator("super_admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.replace(";", ",").split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v

    @property
    def is_local(self) -> bool:
        return (urlparse(self.base_url).hostname or "") in LOCAL_HOSTS

    @property
    def debug_auth_enabled(self) -> bool:
        """The developer login needs both the flag and a localhost BASE_URL. Telegram only works with a
        public HTTPS address, so a production deployment can never satisfy this."""
        return self.debug_auth and self.is_local

    def check(self) -> None:
        """Refuse to start with a configuration that would be unsafe in production."""
        if self.debug_auth and not self.is_local:
            raise RuntimeError("DEBUG_AUTH=true is only allowed when BASE_URL is a localhost address. Remove DEBUG_AUTH.")

    def is_super_admin(self, telegram_id: int | None) -> bool:
        return telegram_id in self.super_admin_ids

    @property
    def bot_api_key(self) -> str:
        """What the bot must send in X-Bot-Key."""
        return self.internal_api_key or self.derive("internal-api")

    def derive(self, purpose: str) -> str:
        """A secret for one purpose, derived from the bot token so nothing extra has to be configured."""
        return hmac.new(self.bot_token.encode(), purpose.encode(), hashlib.sha256).hexdigest()

    @property
    def webapp_url(self) -> str:
        # The version is part of the path (Telegram drops query strings from menu buttons) and changes
        # whenever the web app files change, so phones that cached an old page load the new one
        return f"{self.base_url.rstrip('/')}/webapp/v/{webapp_version()}/"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def doppi_configured(self) -> bool:
        return bool(self.doppi_api_key and self.doppi_refresh_token and self.doppi_org)

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def mail_from(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def sync_times(self) -> list[str]:
        return sorted(x.strip() for x in self.doppi_sync_times.split(",") if x.strip())

    def today(self) -> date:
        from backend import clock

        return clock.now().astimezone(self.tz).date()

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)


@lru_cache
def webapp_version() -> str:
    webapp = Path(__file__).parent / "webapp"
    digest = hashlib.sha256()
    for f in sorted(webapp.rglob("*")):
        if f.is_file():
            digest.update(f.name.encode() + f.read_bytes())
    return digest.hexdigest()[:10]


@lru_cache
def get_settings() -> Settings:
    return Settings()
