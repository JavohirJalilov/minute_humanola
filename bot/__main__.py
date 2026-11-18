"""Run the bot:  python -m bot

With WEBHOOK_URL set, Telegram delivers updates to that address (a webhook); otherwise the bot polls
Telegram for them. Both modes run the same handlers and the same delivery loop."""

import logging

from telegram import Update

from bot.app import build
from bot.config import get_settings

ALLOWED_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request line, and Telegram's contain the bot token (NFR-4)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set")
    application = build(settings)
    if settings.webhook_url:
        if not settings.webhook_url.startswith("https://"):
            raise SystemExit("WEBHOOK_URL must be an https:// address: Telegram only delivers to HTTPS")
        # Registers the address with Telegram (setWebhook) and serves it; Telegram must show the secret token
        application.run_webhook(
            listen=settings.webhook_listen,
            port=settings.webhook_port,
            url_path=settings.webhook_path,
            webhook_url=settings.webhook_url,
            secret_token=settings.webhook_secret_token,
            allowed_updates=ALLOWED_UPDATES,
        )
    else:
        application.run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
