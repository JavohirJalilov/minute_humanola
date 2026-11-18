"""The one place the current time comes from, so tests can move it."""

from datetime import datetime, timedelta, timezone

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def shift(delta: timedelta) -> None:
    """Tests only: move the clock forward."""
    global _offset
    _offset += delta


def reset() -> None:
    global _offset
    _offset = timedelta(0)
