"""The email that carries a verification code (EXT-13). It is written in the worker's language and names
the bot, so the worker recognises it (LNK-9). The code itself is never logged."""

import logging
from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib

from backend.config import Settings

log = logging.getLogger(__name__)

# What the bot is called in emails until it has introduced itself to the backend
DEFAULT_BOT_NAME = "UseMinute bot"

TEXTS = {
    "en": (
        "{code} is your code for {bot}",
        "Your code for {bot} in Telegram:\n\n    {code}\n\n"
        "Type it in the bot's app to link your UseMinute account. The code works for 10 minutes and only once.\n\n"
        "If you did not ask for this code, ignore this email.",
    ),
    "uz": (
        "{code} — {bot} uchun kodingiz",
        "Telegramdagi {bot} uchun kodingiz:\n\n    {code}\n\n"
        "UseMinute hisobingizni ulash uchun uni bot ilovasiga kiriting. Kod 10 daqiqa amal qiladi va faqat bir marta ishlatiladi.\n\n"
        "Agar bu kodni so'ramagan bo'lsangiz, ushbu xatga e'tibor bermang.",
    ),
    "ru": (
        "{code} — ваш код для {bot}",
        "Ваш код для {bot} в Telegram:\n\n    {code}\n\n"
        "Введите его в приложении бота, чтобы привязать аккаунт UseMinute. Код действует 10 минут и только один раз.\n\n"
        "Если вы не запрашивали этот код, не обращайте внимания на это письмо.",
    ),
}


class MailError(Exception):
    pass


def build(settings: Settings, to: str, code: str, lang: str, bot_name: str | None = None, bot_username: str | None = None) -> EmailMessage:
    subject, body = TEXTS.get(lang, TEXTS["en"])
    name = bot_name or DEFAULT_BOT_NAME
    bot = f"{name} (@{bot_username})" if bot_username else name
    message = EmailMessage()
    message["From"] = formataddr((name, settings.mail_from))
    message["To"] = to
    message["Subject"] = subject.format(code=code, bot=name)
    message.set_content(body.format(code=code, bot=bot))
    return message


async def send_verification(settings: Settings, to: str, code: str, lang: str,
                            bot_name: str | None = None, bot_username: str | None = None) -> None:
    """Send the code, or raise MailError. A failed send is not counted against the worker (EXT-15)."""
    if not settings.mail_configured:
        if settings.debug_auth_enabled:  # developer machine only: there is no mailbox to read
            log.warning("SMTP is not set. Development verification code: %s", code)
            return
        raise MailError("The email service is not set up (SMTP_HOST)")
    try:
        await aiosmtplib.send(
            build(settings, to, code, lang, bot_name, bot_username),
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_security == "starttls",
            use_tls=settings.smtp_security == "ssl",
            timeout=20,
        )
    except (aiosmtplib.SMTPException, OSError, TimeoutError) as e:
        # The address is personal data: log only what kind of failure it was (NFR-3)
        log.error("Verification email could not be sent: %s", type(e).__name__)
        raise MailError("The email could not be sent") from e
