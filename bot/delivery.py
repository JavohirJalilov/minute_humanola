"""Delivering the backend's messages.

The backend queues every message it wants sent (a question about a transfer, a notice, an Excel file,
an update to an earlier message). This loop collects them, sends them through Telegram and reports what
happened: sent (with the message id), try again later, or can't be delivered.
"""

import asyncio
import logging
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, WebAppInfo
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

from bot.backend import Backend, BackendError, BackendUnavailable

log = logging.getLogger(__name__)

WAIT = 20  # seconds a call to the backend stays open waiting for the next message
PACE = 0.04  # seconds between messages: Telegram allows about 30 a second


def markup(buttons: list | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = []
    for row in buttons:
        rows.append([
            InlineKeyboardButton(b["text"], web_app=WebAppInfo(url=b["web_app"])) if b.get("web_app")
            else InlineKeyboardButton(b["text"], callback_data=b["data"])
            for b in row
        ])
    return InlineKeyboardMarkup(rows)


def _seconds(value) -> float:
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


async def deliver(bot: Bot, backend: Backend, message: dict) -> dict:
    """Send one message and say how it went."""
    result = {"id": message["id"], "ok": False}
    try:
        if message["kind"] == "edit":
            await bot.edit_message_text(message["text"], chat_id=message["chat_id"], message_id=message["message_id"],
                                        reply_markup=markup(message.get("buttons")))
        elif message["kind"] == "document":
            content, filename = await backend.document(message["id"])
            sent = await bot.send_document(message["chat_id"], InputFile(content, filename=filename),
                                           caption=message.get("text") or None)
            result["message_id"] = sent.message_id
        else:
            sent = await bot.send_message(message["chat_id"], message["text"], reply_markup=markup(message.get("buttons")))
            result["message_id"] = sent.message_id
        result["ok"] = True
    except RetryAfter as e:
        result.update(error="RetryAfter", retry_after=_seconds(e.retry_after) + 1)
    except Forbidden:
        result["error"] = "Forbidden"  # the person blocked the bot: the item stays visible in the Web App
    except BadRequest as e:
        if "not modified" in str(e).lower():
            result["ok"] = True  # the message already shows this
        else:
            result["error"] = "BadRequest"
            log.warning("Message %s refused by Telegram: %s", message["id"], str(e)[:120])
    except NetworkError as e:
        result.update(error=type(e).__name__, retry_after=15)
    except (BackendError, BackendUnavailable) as e:
        result.update(error=type(e).__name__, retry_after=30)
    except TelegramError as e:
        result["error"] = type(e).__name__
    return result


async def flush(bot: Bot, backend: Backend, wait: float = 0) -> int:
    """Collect and send what is waiting. Returns how many messages were handled."""
    messages = await backend.claim(wait=wait)
    results = []
    for message in messages:
        results.append(await deliver(bot, backend, message))
        await asyncio.sleep(PACE)
    for attempt in range(5):
        if not results:
            break
        try:
            await backend.report(results)
            break
        except BackendUnavailable:
            await asyncio.sleep(2 * (attempt + 1))
    return len(messages)


async def run(bot: Bot, backend: Backend) -> None:
    """Deliver forever."""
    while True:
        try:
            await flush(bot, backend, wait=WAIT)
        except asyncio.CancelledError:
            raise
        except BackendUnavailable:
            await asyncio.sleep(3)
        except Exception:
            log.exception("Delivery failed")
            await asyncio.sleep(3)
