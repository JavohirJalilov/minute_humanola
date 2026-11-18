"""The API the bot uses. Everything the bot knows or changes goes through here; it has no database of
its own. Callers must show the bot key (X-Bot-Key)."""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, db, roles
from backend.config import Settings
from backend.deps import SessionDep, SettingsDep, bot_only
from backend.errors import AppError, not_found
from backend.models import AWAITING, AppState, Transfer, User
from backend.services import notify, outbox, registration, transfers

router = APIRouter(prefix="/internal", tags=["bot"], dependencies=[Depends(bot_only)])

MAX_WAIT = 25  # seconds a bot may wait for the next message


class HelloIn(BaseModel):
    name: str = ""
    username: str = ""


class LookupIn(BaseModel):
    telegram_id: int
    username: str | None = None


class TextIn(BaseModel):
    text: str = ""


class RegisterIn(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str
    phone: str
    phone_verified: bool = False
    language: str = "uz"


class LanguageIn(BaseModel):
    telegram_id: int
    language: str


class AnswerIn(BaseModel):
    telegram_id: int
    accept: bool
    reason: str = Field("", max_length=500)


class ClaimIn(BaseModel):
    limit: int = Field(25, ge=1, le=100)
    wait: float = Field(0, ge=0, le=MAX_WAIT)


class Result(BaseModel):
    id: int
    ok: bool = False
    message_id: int | None = None
    error: str | None = None
    retry_after: float | None = None


class ReportIn(BaseModel):
    results: list[Result]


def _user(user: User, settings: Settings) -> dict:
    return {"id": user.id, "name": user.full_name, "language": user.language, "blocked": user.is_blocked,
            "role": roles.role_of(user, settings)}


def _webapp_url(settings: Settings) -> str | None:
    """Telegram accepts only HTTPS addresses for Web App buttons."""
    return settings.webapp_url if settings.base_url.startswith("https://") else None


@router.post("/hello")
async def hello(body: HelloIn, session: SessionDep, settings: SettingsDep) -> dict:
    """The bot introduces itself at startup: its name appears in verification emails (LNK-9)."""
    state = await session.get(AppState, "bot")
    value = {"name": body.name, "username": body.username}
    if state is None:
        session.add(AppState(key="bot", value=value))
    else:
        state.value = value
    await session.commit()
    return {"webapp_url": _webapp_url(settings), "timezone": settings.timezone}


# ---- registration (section 4)


@router.post("/users/lookup")
async def lookup(body: LookupIn, session: SessionDep, settings: SettingsDep) -> dict:
    """Is this Telegram account registered? Also keeps the stored Telegram username current (ONB-12)."""
    user = await registration.find_user(session, body.telegram_id)
    if user is not None and body.username != user.username:
        user.username = body.username
        await session.commit()
    return {"registered": user is not None, "user": _user(user, settings) if user else None, "webapp_url": _webapp_url(settings)}


@router.post("/registration/name")
async def check_name(body: TextIn) -> dict:
    """ONB-1: the cleaned name, or why it is not accepted."""
    return {"name": registration.clean_name(body.text)}


@router.post("/registration/phone")
async def check_phone(body: TextIn, session: SessionDep) -> dict:
    """ONB-4 and ONB-7: the number in international format, or why it is not accepted."""
    phone = registration.clean_phone(body.text)
    await registration.phone_must_be_free(session, phone)
    return {"phone": phone}


@router.post("/registration")
async def register(body: RegisterIn, session: SessionDep, settings: SettingsDep) -> dict:
    """Save the account with the Worker role. Registering twice returns the same account (ONB-8)."""
    user = await registration.register(
        session,
        telegram_id=body.telegram_id,
        username=body.username,
        full_name=registration.clean_name(body.full_name),
        phone=registration.clean_phone(body.phone),
        phone_verified=body.phone_verified,
        language=body.language,
    )
    return {"user": _user(user, settings), "webapp_url": _webapp_url(settings)}


@router.post("/users/language")
async def set_language(body: LanguageIn, session: SessionDep, settings: SettingsDep) -> dict:
    if body.language not in registration.LANGUAGES:
        raise AppError("language_invalid", "Choose Uzbek, Russian or English")
    user = await registration.find_user(session, body.telegram_id)
    if user is None:
        raise not_found("This person was not found")
    user.language = body.language
    await session.commit()
    return {"user": _user(user, settings)}


# ---- answering a transfer from the chat (PAY-2)


async def _receiver(session: AsyncSession, telegram_id: int) -> User:
    user = await registration.find_user(session, telegram_id)
    if user is None:
        raise AppError("not_registered", "You are not registered yet. Open the bot, press Start and register first.", 403)
    if user.is_blocked:
        raise AppError("blocked", "Your access has been disabled. Contact an administrator.", 403)
    return user


@router.get("/transfers/{transfer_id}")
async def transfer(transfer_id: int, telegram_id: int, session: SessionDep) -> dict:
    user = await _receiver(session, telegram_id)
    found = await session.get(Transfer, transfer_id)
    if found is None or found.receiver_id != user.id:
        raise not_found("This transfer was not found")
    return {"id": found.id, "kind": found.kind, "state": found.state, "amount": found.amount, "can_answer": found.state == AWAITING}


@router.post("/transfers/{transfer_id}/answer")
async def answer(transfer_id: int, body: AnswerIn, session: SessionDep, settings: SettingsDep) -> dict:
    """Accept or decline. Answered already (here or in the Web App): 409 `already_answered`, and the
    chat messages are brought up to date (PAY-5, NTF-3)."""
    user = await _receiver(session, body.telegram_id)
    try:
        found = await transfers.answer(session, settings, user, transfer_id, body.accept, body.reason)
    except AppError as e:
        if e.code == "already_answered":
            await notify.refresh_messages(session, await session.get(Transfer, transfer_id))
            await session.commit()
        raise
    return {"id": found.id, "kind": found.kind, "state": found.state}


# ---- delivering messages


@router.post("/outbox/claim")
async def claim(body: ClaimIn) -> dict:
    """The next messages to send. With `wait`, the call stays open until there is something to send
    (or the time is up), so messages go out the moment they are written."""
    deadline = clock.now() + timedelta(seconds=body.wait)
    while True:
        async with db.ReadSession() as session:
            ready = await outbox.has_ready(session)
        if ready:
            async with db.WriteSession() as session:
                messages = await outbox.claim(session, body.limit)
            if messages:
                return {"messages": messages}
        left = (deadline - clock.now()).total_seconds()
        if left <= 0:
            return {"messages": []}
        await outbox.wait(min(left, 2))  # also notices messages written by another backend process
        await asyncio.sleep(0)


@router.post("/outbox/report")
async def report(body: ReportIn, session: SessionDep) -> dict:
    await outbox.report(session, [r.model_dump() for r in body.results])
    return {"ok": True}


@router.get("/outbox/{outbox_id}/document")
async def document(outbox_id: int, session: SessionDep) -> Response:
    found = await outbox.document_of(session, outbox_id)
    if found is None:
        raise not_found("This file was not found")
    content, filename = found
    return Response(content, media_type="application/octet-stream", headers={"X-Filename": filename})
