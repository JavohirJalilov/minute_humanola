"""Putting the bot together: python-telegram-bot's Application, the connection to the backend, the
menu button and commands, and the loop that delivers the backend's messages."""

import asyncio
import logging
from pathlib import Path

from telegram import BotCommand, MenuButtonWebApp, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, Defaults, PersistenceInput, PicklePersistence

from bot import delivery, handlers
from bot.backend import Backend, BackendUnavailable
from bot.config import BotSettings
from bot.texts import LANGUAGES, t

log = logging.getLogger(__name__)

MENU_TEXT = "UseMinute"
COMMANDS = (("start", "Register or open the app"), ("app", "Open the app"), ("language", "Change the language"), ("help", "Help"))
CHECK_MENU_EVERY = 600  # seconds


def build(settings: BotSettings, backend: Backend | None = None, request=None) -> Application:
    """The bot, ready to run. `backend` and `request` are replaced in tests."""
    builder = Application.builder().token(settings.bot_token).defaults(Defaults(parse_mode=ParseMode.HTML))
    builder = builder.post_init(_on_start).post_stop(_on_stop)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    if settings.bot_state_file and request is None:
        # Conversation steps survive a restart. Only user_data is kept
        Path(settings.bot_state_file).parent.mkdir(parents=True, exist_ok=True)
        keep = PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False)
        builder = builder.persistence(PicklePersistence(filepath=settings.bot_state_file, store_data=keep))
    application = builder.build()
    application.bot_data["backend"] = backend or Backend(settings.backend_url, settings.api_key)
    application.bot_data["tasks"] = []
    handlers.add_handlers(application)
    return application


async def introduce(application: Application) -> str | None:
    """Tell the backend the bot's name (it appears in verification emails) and learn the Web App address."""
    backend: Backend = application.bot_data["backend"]
    me = application.bot.bot
    info = await backend.hello(me.full_name, me.username or "")
    return info.get("webapp_url")


async def set_menu(application: Application, url: str | None) -> None:
    """EXT-1: the Web App is reachable at any time from the menu button next to the message field."""
    if not url or application.bot_data.get("menu_url") == url:
        return
    await application.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=MENU_TEXT, web_app=WebAppInfo(url=url)))
    application.bot_data["menu_url"] = url


async def _keep_menu_current(application: Application) -> None:
    """The Web App address changes with every release of its files: keep the menu button pointing at it."""
    while True:
        delay = CHECK_MENU_EVERY
        try:
            await set_menu(application, await introduce(application))
        except asyncio.CancelledError:
            raise
        except (BackendUnavailable, TelegramError):
            delay = 5  # the backend is still starting, or Telegram hiccuped: try again shortly
        except Exception:
            log.exception("Menu button check failed")
        await asyncio.sleep(delay)


async def _on_start(application: Application) -> None:
    for lang in LANGUAGES:
        commands = [BotCommand(name, t(lang, text)) for name, text in COMMANDS]
        # Uzbek is what people see unless their Telegram is set to Russian or English
        await application.bot.set_my_commands(commands, language_code=None if lang == "uz" else lang)
    backend: Backend = application.bot_data["backend"]
    application.bot_data["tasks"] = [
        asyncio.create_task(_keep_menu_current(application)),
        asyncio.create_task(delivery.run(application.bot, backend)),
    ]
    log.info("Bot @%s started", application.bot.username)


async def _on_stop(application: Application) -> None:
    for task in application.bot_data.get("tasks", []):
        task.cancel()
    await application.bot_data["backend"].close()
