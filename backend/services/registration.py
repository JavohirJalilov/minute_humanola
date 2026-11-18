"""Registration in the bot chat (section 4): a full name, then a phone number."""

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock
from backend.errors import AppError
from backend.models import User
from backend.services import audit

NAME_MIN, NAME_MAX = 2, 128
LANGUAGES = ("uz", "ru", "en")
# Characters a name may have besides letters: O'g'li, Mirzo-Ulug'bek, A. Navoiy
NAME_PUNCTUATION = set(" -‐‑–'’‘ʻʼ`.")
PHONE_IGNORED = re.compile(r"[\s\-‐‑–()\[\]]")


def default_language(telegram_language: str | None) -> str:
    """Uzbek, or Russian when the person's Telegram is set to Russian (NFR-15)."""
    return "ru" if (telegram_language or "").lower().startswith("ru") else "uz"


def clean_name(text: str | None) -> str:
    """ONB-1: 2 to 128 characters after trimming extra spaces, at least two words, not a command."""
    raw = text or ""
    if raw.lstrip().startswith("/"):
        raise AppError("name_command", "That is a command, not a name. Please send your first and last name.")
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise AppError("name_length", "The name must be 2 to 128 characters. Please send your first and last name.")
    if any(not (ch in NAME_PUNCTUATION or unicodedata.category(ch)[0] in "LM") for ch in name):
        raise AppError("name_chars", "A name has only letters. Please send your first and last name.")
    words = [w for w in name.split(" ") if any(unicodedata.category(ch)[0] == "L" for ch in w)]
    if len(words) < 2:
        raise AppError("name_words", "Please send both your first name and your last name.")
    return name


def clean_phone(text: str | None) -> str:
    """ONB-4: spaces, dashes and brackets are ignored; 9 to 15 digits with an optional leading +.
    A 9-digit number is an Uzbek number. Returns the international format, +998XXXXXXXXX."""
    raw = PHONE_IGNORED.sub("", text or "")
    digits = raw[1:] if raw.startswith("+") else raw
    if not digits.isascii() or not digits.isdigit() or not 9 <= len(digits) <= 15:
        raise AppError("phone_invalid", "That doesn't look like a phone number. Send it like +998 90 123 45 67, or tap the button.")
    if len(digits) == 9:
        digits = "998" + digits
    return "+" + digits


async def find_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


TAKEN = "This phone number is already used by another account. Please contact an administrator."


async def phone_must_be_free(session: AsyncSession, phone: str, telegram_id: int | None = None) -> None:
    """ONB-7: a phone number already used by another account is refused."""
    owner = await session.scalar(select(User.telegram_id).where(User.phone == phone))
    if owner is not None and owner != telegram_id:
        raise AppError("phone_taken", TAKEN, 409)


async def register(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    full_name: str,
    phone: str,
    phone_verified: bool,
    language: str,
) -> User:
    """Save the account with the Worker role (ROLE-1). One Telegram account is one user (ONB-8) and a
    phone number belongs to one account (ONB-7). Commits."""
    existing = await find_user(session, telegram_id)
    if existing is not None:
        return existing
    await phone_must_be_free(session, phone)
    user = User(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        phone=phone,
        phone_verified=phone_verified,
        role="worker",
        language=language if language in LANGUAGES else "uz",
        registered_at=clock.now(),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # Two messages raced: the account or the phone number was saved a moment ago
        await session.rollback()
        existing = await find_user(session, telegram_id)
        if existing is not None:
            return existing
        raise AppError("phone_taken", TAKEN, 409)
    audit.record(session, user.id, "user.registered", target_user_id=user.id, phone_verified=phone_verified)
    await session.commit()
    return user
