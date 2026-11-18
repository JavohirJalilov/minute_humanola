"""Shared test setup: a fresh SQLite database per test, the backend app called in-process, and helpers
to put people, phones, recordings, rates and money in place."""

import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

BOT_TOKEN = "123456:TEST-token"
os.environ.update(
    ENV_FILE="/nonexistent/.env",  # never read the real configuration
    BOT_TOKEN=BOT_TOKEN,
    BASE_URL="https://pay.test",
    SUPER_ADMIN_IDS="9999",
    DEBUG_AUTH="false",
    RUN_JOBS="false",
    TIMEZONE="Asia/Tashkent",
    DOPPI_API_KEY="",
    DOPPI_REFRESH_TOKEN="",
    DOPPI_ORG="",
    SMTP_HOST="",
    BACKUP_TIME="",
)

import httpx  # noqa: E402

from backend import clock, db, texts  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.models import (  # noqa: E402
    ACCEPTED,
    FUNDING,
    PAYMENT,
    DoppiLink,
    InviteCode,
    Phone,
    Rate,
    Recording,
    StaffAssignment,
    Transfer,
    User,
)

texts.STRICT = True  # a bot message without a translation fails the test
TODAY = date(2026, 11, 16)


def make_init_data(telegram_id: int, auth_date: int | None = None, token: str = BOT_TOKEN) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH",
        "user": json.dumps({"id": telegram_id, "first_name": "Test"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def auth(who) -> dict:
    """Web App headers for a user (or a Telegram id)."""
    return {"Authorization": f"tma {make_init_data(who if isinstance(who, int) else who.telegram_id)}"}


def bot_key() -> dict:
    return {"X-Bot-Key": get_settings().bot_api_key}


@pytest.fixture(autouse=True)
async def database(tmp_path):
    get_settings.cache_clear()
    clock.reset()
    db.init_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.create_tables()
    yield
    await db.dispose()
    clock.reset()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
async def client():
    from backend.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---- putting data in place


async def add_user(telegram_id: int, name: str, role: str = "worker", **kw) -> User:
    async with db.WriteSession() as s:
        user = User(telegram_id=telegram_id, full_name=name, phone=f"+99890{telegram_id:07d}", phone_verified=True,
                    role=role, language=kw.pop("language", "en"), registered_at=clock.now(), **kw)
        s.add(user)
        await s.commit()
        return user


async def link(user: User, email: str, code_id: int | None = None) -> None:
    async with db.WriteSession() as s:
        s.add(DoppiLink(user_id=user.id, email=email, invite_code_id=code_id, verified_at=clock.now(), keep_recordings=True))
        if await s.get(Phone, email) is None:
            s.add(Phone(email=email, recordings=0))
        await s.commit()


async def assign(worker: User, staff: User, at: datetime | None = None) -> None:
    async with db.WriteSession() as s:
        s.add(StaffAssignment(worker_id=worker.id, staff_id=staff.id, started_at=at or clock.now(), source="admin"))
        await s.commit()


async def add_rate(amount: int | None, start: date = date(2026, 1, 1), scope: str = "company", user: User | None = None) -> None:
    async with db.WriteSession() as s:
        s.add(Rate(scope=scope, user_id=user.id if user else None, amount=amount, start_date=start, created_at=clock.now()))
        await s.commit()


_n = 0


async def add_recording(email: str, minutes: float, status: str = "accepted", day: date = TODAY, **kw) -> str:
    """A recording as a sync would store it. `status` is accepted, pending or rejected."""
    global _n
    _n += 1
    recorded = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(seconds=_n)
    rid = kw.pop("id", f"rec-{_n}")
    async with db.WriteSession() as s:
        s.add(Recording(
            id=rid, user_key="u1", email=email, recorded_at=recorded, uploaded_at=recorded + timedelta(hours=1), day=day,
            minutes=minutes, duration_ms=round(minutes * 60000), review_status="flagged" if status == "rejected" else status,
            status=status, first_seen_at=clock.now(), synced_at=clock.now(), clock_fixed=False, **kw,
        ))
        await s.commit()
    return rid


async def add_code(code: str = "87GSGM84", staff: User | None = None, active: bool = True) -> InviteCode:
    async with db.WriteSession() as s:
        invite = InviteCode(code=code, label="Team", staff_id=staff.id if staff else None, is_active=active, created_at=clock.now())
        s.add(invite)
        await s.commit()
        return invite


async def add_transfer(kind: str, sender: User, receiver: User, amount: int, state: str = ACCEPTED, **kw) -> Transfer:
    """A transfer already in a given state, as if it had gone through the lifecycle."""
    async with db.WriteSession() as s:
        now = clock.now()
        t = Transfer(kind=kind, sender_id=sender.id, receiver_id=receiver.id, amount=amount, paid_on=kw.pop("paid_on", TODAY),
                     note=kw.pop("note", ""), state=state, created_at=kw.pop("created_at", now),
                     answered_at=now if state in ("accepted", "declined", "voided") else None, **kw)
        s.add(t)
        await s.commit()
        return t


async def fund(admin: User, staff: User, amount: int, state: str = ACCEPTED) -> Transfer:
    return await add_transfer(FUNDING, admin, staff, amount, state)


async def pay(staff: User, worker: User, amount: int, state: str = ACCEPTED) -> Transfer:
    return await add_transfer(PAYMENT, staff, worker, amount, state)


@pytest.fixture
async def team():
    """A Super Admin, two Admins, two Staff and three Workers. Worker `ali` is linked and assigned to `staff`."""
    class Team:
        pass

    t = Team()
    t.boss = await add_user(9999, "Boss Super")
    t.admin = await add_user(100, "Ada Admin", "admin")
    t.admin2 = await add_user(101, "Adam Admin", "admin")
    t.staff = await add_user(10, "Sam Staff", "staff")
    t.staff2 = await add_user(11, "Sara Staff", "staff")
    t.ali = await add_user(1, "Ali Worker")
    t.bek = await add_user(2, "Bek Worker")
    t.cem = await add_user(3, "Cem Worker")
    await link(t.ali, "cam-01@example.com")
    await assign(t.ali, t.staff)
    return t


# ---- the bot, with Telegram replaced by a recorder


from telegram import Update  # noqa: E402
from telegram.request import BaseRequest  # noqa: E402

from bot import texts as bot_texts  # noqa: E402

bot_texts.STRICT = True


class FakeTelegram(BaseRequest):
    """Stands in for Telegram's servers: records every call the bot makes and answers like Telegram would."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}  # method name → what Telegram answers instead
        self._message_id = 5000

    @property
    def read_timeout(self):
        return 5

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(self, url, method, request_data=None, *args, **kwargs):
        name = url.rsplit("/", 1)[1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((name, params))
        if name in self.fail:
            raise self.fail[name]
        if name == "getMe":
            result = {"id": 42, "is_bot": True, "first_name": "UseMinute test bot", "username": "doppi_test_bot"}
        elif name in ("sendMessage", "sendDocument", "editMessageText"):
            self._message_id += 1
            result = {"message_id": params.get("message_id") or self._message_id, "date": int(time.time()),
                      "chat": {"id": int(params["chat_id"]), "type": "private"}, "text": params.get("text", "")}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    # -- reading what the bot did

    def messages(self, chat_id: int | None = None, method: str = "sendMessage") -> list[dict]:
        return [p for name, p in self.calls if name == method and (chat_id is None or int(p["chat_id"]) == chat_id)]

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [p["text"] for p in self.messages(chat_id)]

    def last(self, chat_id: int | None = None) -> dict:
        return self.messages(chat_id)[-1]

    def clear(self) -> None:
        self.calls.clear()


def buttons_of(message: dict) -> list[dict]:
    """The buttons under (or instead of) a message the bot sent, as flat dicts."""
    markup = message.get("reply_markup") or {}
    rows = markup.get("inline_keyboard") or markup.get("keyboard") or []
    return [b for row in rows for b in row]


class Chat:
    """Talks to the bot the way a person in Telegram would."""

    def __init__(self, application, telegram: FakeTelegram, backend):
        self.application, self.telegram, self.backend = application, telegram, backend
        self._update_id = 0

    def _user(self, telegram_id: int, **fields) -> dict:
        return {"id": telegram_id, "is_bot": False, "first_name": fields.pop("first_name", "Test"), **fields}

    async def _feed(self, payload: dict) -> None:
        self._update_id += 1
        payload["update_id"] = self._update_id
        await self.application.process_update(Update.de_json(payload, self.application.bot))

    async def say(self, telegram_id: int, text: str, **user) -> None:
        message = {"message_id": self._update_id + 1, "date": int(time.time()), "chat": {"id": telegram_id, "type": "private"},
                   "from": self._user(telegram_id, **user), "text": text}
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self._feed({"message": message})

    async def share_contact(self, telegram_id: int, phone: str, owner_id: int | None = None, **user) -> None:
        contact = {"phone_number": phone, "first_name": "Test", "user_id": owner_id if owner_id is not None else telegram_id}
        await self._feed({"message": {"message_id": self._update_id + 1, "date": int(time.time()),
                                      "chat": {"id": telegram_id, "type": "private"}, "from": self._user(telegram_id, **user),
                                      "contact": contact}})

    async def send_photo(self, telegram_id: int, **user) -> None:
        photo = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
        await self._feed({"message": {"message_id": self._update_id + 1, "date": int(time.time()),
                                      "chat": {"id": telegram_id, "type": "private"}, "from": self._user(telegram_id, **user),
                                      "photo": photo}})

    async def tap(self, telegram_id: int, data: str, message_id: int = 1, **user) -> None:
        message = {"message_id": message_id, "date": int(time.time()), "chat": {"id": telegram_id, "type": "private"}, "text": "…"}
        await self._feed({"callback_query": {"id": str(self._update_id + 1), "from": self._user(telegram_id, **user),
                                             "chat_instance": "c", "data": data, "message": message}})

    async def deliver(self) -> int:
        """Let the bot send what the backend queued."""
        from bot import delivery

        total = 0
        while n := await delivery.flush(self.application.bot, self.backend):
            total += n
        return total


@pytest.fixture
async def chat():
    from backend.main import app as backend_app
    from bot.app import build
    from bot.backend import Backend
    from bot.config import BotSettings

    telegram = FakeTelegram()
    backend = Backend("http://backend", get_settings().bot_api_key, transport=httpx.ASGITransport(app=backend_app))
    application = build(BotSettings(bot_token=BOT_TOKEN, bot_state_file=""), backend=backend, request=telegram)
    await application.initialize()
    telegram.clear()
    yield Chat(application, telegram, backend)
    await application.shutdown()
    await backend.close()


# ---- email, with the mail server replaced by a mailbox


class Mailbox:
    def __init__(self):
        self.sent: list[dict] = []
        self.fail = False

    @property
    def code(self) -> str:
        return self.sent[-1]["code"]


@pytest.fixture
def mailbox(monkeypatch):
    from backend.services import mailer

    box = Mailbox()

    async def send(settings, to, code, lang, bot_name=None, bot_username=None):
        if box.fail:
            raise mailer.MailError("down")
        box.sent.append({"to": to, "code": code, "lang": lang, "bot_name": bot_name})

    monkeypatch.setattr(mailer, "send_verification", send)
    return box


async def outbox_rows(purpose: str | None = None, chat_id: int | None = None) -> list:
    """Messages the backend queued for the bot."""
    from sqlalchemy import select

    from backend.models import Outbox

    async with db.ReadSession() as s:
        q = select(Outbox).order_by(Outbox.id)
        if purpose:
            q = q.where(Outbox.purpose == purpose)
        if chat_id:
            q = q.where(Outbox.chat_id == chat_id)
        return list(await s.scalars(q))
