"""Telegram messages on their way to the bot.

The backend never talks to Telegram. A service that changes something adds the messages that announce it
to the outbox in the same transaction (`send`, `edit`, `document`). The bot collects them (`claim`), sends
them and reports back (`report`). A message whose delivery fails stays recorded; the change it announced
is already saved either way (NTF-2).
"""

import asyncio
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock
from backend.models import AWAITING, Outbox, Transfer, TransferMessage, User

MAX_ATTEMPTS = 8
CLAIM_TIMEOUT = timedelta(minutes=2)  # a claimed message nobody reported on is offered again
KEEP = timedelta(days=30)

_wake = asyncio.Event()


def send(session: AsyncSession, user: User, text: str, buttons: list | None = None, *,
         transfer_id: int | None = None, purpose: str = "") -> Outbox:
    """A new message to a person. With `transfer_id` the message asks about that transfer."""
    row = Outbox(kind="send", chat_id=user.telegram_id, text=text, buttons=buttons, transfer_id=transfer_id,
                 purpose=purpose, status="pending", attempts=0, created_at=clock.now())
    session.add(row)
    return row


def edit(session: AsyncSession, chat_id: int, message_id: int, text: str, buttons: list | None = None, *, purpose: str = "") -> Outbox:
    """Change a message the bot sent earlier (the buttons disappear unless given again)."""
    row = Outbox(kind="edit", chat_id=chat_id, message_id=message_id, text=text, buttons=buttons, purpose=purpose,
                 status="pending", attempts=0, created_at=clock.now())
    session.add(row)
    return row


def document(session: AsyncSession, user: User, content: bytes, filename: str, caption: str = "", *, purpose: str = "") -> Outbox:
    """A file sent to a person's chat (PAYA-9)."""
    row = Outbox(kind="document", chat_id=user.telegram_id, text=caption, document=content, filename=filename,
                 purpose=purpose, status="pending", attempts=0, created_at=clock.now())
    session.add(row)
    return row


def wake() -> None:
    """Something may be waiting: let a bot that is listening collect it now."""
    _wake.set()


async def wait(seconds: float) -> None:
    """Wait until something is added in this process, or `seconds` at most."""
    _wake.clear()
    try:
        await asyncio.wait_for(_wake.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _ready():
    now = clock.now()
    return or_(
        and_(Outbox.status == "pending", or_(Outbox.not_before.is_(None), Outbox.not_before <= now)),
        and_(Outbox.status == "claimed", Outbox.claimed_at < now - CLAIM_TIMEOUT),
    )


async def has_ready(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(Outbox.id).where(_ready()).limit(1)))


async def claim(session: AsyncSession, limit: int = 25) -> list[dict]:
    """Hand the next messages to the bot, oldest first. A question about a transfer that was answered in
    the meantime is dropped instead of sent. Commits."""
    now = clock.now()
    rows = list(await session.scalars(select(Outbox).where(_ready()).order_by(Outbox.id).limit(limit)))
    out = []
    for row in rows:
        if row.kind == "send" and row.transfer_id is not None:
            state = await session.scalar(select(Transfer.state).where(Transfer.id == row.transfer_id))
            if state != AWAITING:
                row.status, row.done_at = "skipped", now
                continue
        row.status, row.claimed_at = "claimed", now
        row.attempts += 1
        out.append({
            "id": row.id,
            "kind": row.kind,
            "chat_id": row.chat_id,
            "message_id": row.message_id,
            "text": row.text,
            "buttons": row.buttons,
            "filename": row.filename,
        })
    await session.commit()
    return out


async def report(session: AsyncSession, results: list[dict]) -> None:
    """What happened to claimed messages: `ok` with the Telegram message id, or an error; with
    `retry_after` (seconds) the message is tried again later. Commits."""
    from backend.services import notify  # notify builds on this module

    now = clock.now()
    for result in results:
        row = await session.get(Outbox, result.get("id"))
        if row is None or row.status != "claimed":
            continue
        if result.get("ok"):
            row.status, row.done_at, row.error, row.document = "sent", now, None, None
            if row.kind == "send" and row.transfer_id is not None and result.get("message_id"):
                session.add(TransferMessage(transfer_id=row.transfer_id, chat_id=row.chat_id, message_id=int(result["message_id"])))
                await session.flush()
                transfer = await session.get(Transfer, row.transfer_id)
                if transfer is not None and transfer.state != AWAITING:
                    # Answered while this message was on its way: take its buttons off right away
                    await notify.refresh_messages(session, transfer)
        elif result.get("retry_after") is not None and row.attempts < MAX_ATTEMPTS:
            row.status = "pending"
            row.not_before = now + timedelta(seconds=min(max(float(result["retry_after"]), 1), 3600))
            row.error = str(result.get("error") or "")[:200] or None
        else:
            row.status, row.done_at, row.document = "failed", now, None
            row.error = str(result.get("error") or "")[:200] or None
    await session.commit()
    wake()


async def document_of(session: AsyncSession, outbox_id: int) -> tuple[bytes, str] | None:
    row = await session.get(Outbox, outbox_id)
    if row is None or row.document is None:
        return None
    return row.document, row.filename or "file"


async def tidy(session: AsyncSession) -> None:
    """Forget delivered messages after a month. Commits."""
    await session.execute(
        delete(Outbox).where(Outbox.status.in_(("sent", "failed", "skipped")), Outbox.created_at < clock.now() - KEEP)
    )
    await session.commit()
