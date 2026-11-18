"""How values are written in bot messages, emails and Excel files (NFR-16)."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

NBSP = " "
CURRENCY = {"uz": "so'm", "en": "so'm", "ru": "сум"}
HOURS = {"uz": ("soat", "daq"), "en": ("h", "m"), "ru": ("ч", "мин")}


def number(amount: int) -> str:
    """1 500 000"""
    return f"{amount:,}".replace(",", NBSP)


def money(amount: int | None, lang: str = "en") -> str:
    """1 500 000 so'm"""
    if amount is None:
        return "—"
    return f"{number(amount)}{NBSP}{CURRENCY.get(lang, CURRENCY['en'])}"


def whole_minutes(minutes: Decimal | float | int) -> int:
    return int(Decimal(str(minutes)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def duration(minutes: Decimal | float | int, lang: str = "en") -> str:
    """13h 30m"""
    total = whole_minutes(minutes)
    h, m = HOURS.get(lang, HOURS["en"])
    if lang == "en":
        return f"{total // 60}{h} {total % 60:02d}{m}"
    return f"{total // 60}{NBSP}{h} {total % 60:02d}{NBSP}{m}"


def day(value: date) -> str:
    """DD.MM.YYYY"""
    return f"{value:%d.%m.%Y}"


def moment(value: datetime, tz: ZoneInfo) -> str:
    """DD.MM.YYYY HH:MM in the configured timezone"""
    return f"{value.astimezone(tz):%d.%m.%Y %H:%M}"


def mask_phone(phone: str) -> str:
    """+998 •• ••• •• 41: enough to tell people apart in a list without showing the number."""
    digits = phone.lstrip("+")
    if len(digits) == 12 and digits.startswith("998"):
        return f"+998 •• ••• •• {digits[-2:]}"
    if len(digits) <= 4:
        return "+" + "•" * len(digits)
    return f"+{digits[:2]} {'•' * (len(digits) - 4)} {digits[-2:]}"


def pretty_phone(phone: str) -> str:
    """+998 90 123 45 67"""
    digits = phone.lstrip("+")
    if len(digits) == 12 and digits.startswith("998"):
        return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:]}"
    return phone
