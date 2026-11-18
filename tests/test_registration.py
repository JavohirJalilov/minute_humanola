"""Section 4: registration in the bot chat (ONB-1..12), end to end: bot handlers → backend API → database."""

import pytest
from sqlalchemy import select

from backend import db
from backend.errors import AppError
from backend.models import AuditLog, User
from backend.services import registration
from tests.conftest import add_user, auth, buttons_of

PROFILE = {"first_name": "Aziz", "last_name": "Karimov", "username": "aziz_k", "language_code": "en"}


async def begin(chat, telegram_id: int) -> None:
    """Press Start and choose English, so the tests can read the bot's answers."""
    await chat.say(telegram_id, "/start", **PROFILE)
    await chat.tap(telegram_id, "lang:en", **PROFILE)


async def the_user(telegram_id: int) -> User | None:
    async with db.ReadSession() as s:
        return await s.scalar(select(User).where(User.telegram_id == telegram_id))


# ---- the whole flow


async def test_start_name_phone_registers_a_worker(chat, client):
    await chat.say(7, "/start", **PROFILE)
    welcome, question = chat.telegram.messages(7)
    assert "ism va familiyangizni" in question["text"]  # NFR-15: Uzbek unless Telegram is set to Russian
    assert [b["text"] for b in buttons_of(question)] == ["Aziz Karimov"]  # ONB-2: one-tap suggestion
    assert {b["callback_data"] for b in buttons_of(welcome)} == {"lang:uz", "lang:ru", "lang:en"}
    await chat.tap(7, "lang:en", **PROFILE)
    assert "first and last name" in chat.telegram.last(7)["text"]

    await chat.say(7, "  Aziz   Karimov ", **PROFILE)
    ask_phone = chat.telegram.last(7)
    assert "phone number" in ask_phone["text"]
    assert buttons_of(ask_phone)[0]["request_contact"] is True  # the Share phone number button

    await chat.share_contact(7, "998901234567", **PROFILE)
    done, open_app = chat.telegram.messages(7)[-2:]
    assert "You are registered, Aziz Karimov" in done["text"]
    assert done["reply_markup"] == {"remove_keyboard": True}
    assert buttons_of(open_app)[0]["web_app"]["url"].startswith("https://pay.test/webapp/v/")  # ONB-5: Open app

    user = await the_user(7)
    assert (user.full_name, user.phone, user.phone_verified, user.role) == ("Aziz Karimov", "+998901234567", True, "worker")
    assert (user.username, user.language, user.is_blocked) == ("aziz_k", "en", False)  # ONB-12
    assert user.registered_at is not None
    # ONB-10: no approval step, the Web App works at once
    r = await client.get("/api/me", headers=auth(7))
    assert r.status_code == 200 and r.json()["user"]["role"] == "worker" and r.json()["start"] == "progress"


async def test_typed_phone_is_stored_as_unverified(chat):
    await begin(chat, 8)
    await chat.say(8, "Aziz Karimov", **PROFILE)
    await chat.say(8, "90 123-45 (67)", **PROFILE)  # ONB-4: 9 digits = an Uzbek number
    user = await the_user(8)
    assert user.phone == "+998901234567" and user.phone_verified is False  # ONB-5


async def test_registration_is_audited(chat):
    await begin(chat, 8)
    await chat.say(8, "Aziz Karimov", **PROFILE)
    await chat.say(8, "+998 90 123 45 67", **PROFILE)
    async with db.ReadSession() as s:
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "user.registered"))
    assert entry is not None and entry.details == {"phone_verified": False}


# ---- each rule


@pytest.mark.parametrize("text, code", [
    ("Aziz", "name_words"),  # one word
    ("A", "name_length"),
    ("x" * 70 + " " + "y" * 70, "name_length"),  # 141 characters
    ("Aziz Karimov 2", "name_chars"),
    ("Aziz K@rimov", "name_chars"),
    ("/Aziz Karimov", "name_command"),
    ("- -", "name_words"),
])
def test_names_that_are_refused(text, code):
    with pytest.raises(AppError) as e:
        registration.clean_name(text)
    assert e.value.code == code


@pytest.mark.parametrize("text, name", [
    ("Aziz Karimov", "Aziz Karimov"),
    ("  Aziz \n  Karimov  ", "Aziz Karimov"),  # extra spaces are trimmed
    ("O'g'iloy Mirzo-Ulug'bek qizi", "O'g'iloy Mirzo-Ulug'bek qizi"),
    ("Азиз Каримов", "Азиз Каримов"),
    ("x" * 63 + " " + "y" * 64, "x" * 63 + " " + "y" * 64),  # exactly 128
])
def test_names_that_are_accepted(text, name):
    assert registration.clean_name(text) == name


@pytest.mark.parametrize("text, phone", [
    ("901234567", "+998901234567"),
    ("+998 (90) 123-45-67", "+998901234567"),
    ("998901234567", "+998901234567"),
    ("+7 912 345 67 89", "+79123456789"),
    ("+123456789012345", "+123456789012345"),  # 15 digits
])
def test_phones_that_are_accepted(text, phone):
    assert registration.clean_phone(text) == phone


@pytest.mark.parametrize("text", ["12345678", "+1234567890123456", "90 123 45 6x", "", "phone", "++998901234567"])
def test_phones_that_are_refused(text):
    with pytest.raises(AppError) as e:
        registration.clean_phone(text)
    assert e.value.code == "phone_invalid"


async def test_invalid_name_repeats_the_question(chat):
    await begin(chat, 7)
    await chat.say(7, "Aziz", **PROFILE)
    assert "first name and your last name" in chat.telegram.last(7)["text"]  # ONB-6
    await chat.send_photo(7, **PROFILE)
    assert "as a text message" in chat.telegram.last(7)["text"]  # ONB-1: text only
    await chat.say(7, "/skip", **PROFILE)
    assert "command, not a name" in chat.telegram.last(7)["text"]  # ONB-1, and there is no Skip (ONB-6)
    assert await the_user(7) is None
    await chat.say(7, "Aziz Karimov", **PROFILE)
    assert "phone number" in chat.telegram.last(7)["text"]


async def test_someone_elses_contact_is_refused(chat):
    await begin(chat, 7)
    await chat.say(7, "Aziz Karimov", **PROFILE)
    await chat.share_contact(7, "998901234567", owner_id=555, **PROFILE)  # ONB-3: a forwarded contact
    assert "your own" in chat.telegram.last(7)["text"]
    assert await the_user(7) is None


async def test_invalid_phone_repeats_the_question(chat):
    await begin(chat, 7)
    await chat.say(7, "Aziz Karimov", **PROFILE)
    await chat.say(7, "12345", **PROFILE)
    last = chat.telegram.last(7)
    assert "doesn't look like a phone number" in last["text"] and buttons_of(last)[0]["request_contact"]
    await chat.say(7, "/skip", **PROFILE)  # no Skip at this step either
    assert "phone number" in chat.telegram.last(7)["text"]
    assert await the_user(7) is None


async def test_phone_of_another_account_is_refused(chat):
    other = await add_user(50, "Other Person")
    await begin(chat, 7)
    await chat.say(7, "Aziz Karimov", **PROFILE)
    await chat.say(7, other.phone, **PROFILE)
    assert "already used by another account" in chat.telegram.last(7)["text"]  # ONB-7
    assert await the_user(7) is None
    await chat.say(7, "+998 90 000 00 01", **PROFILE)  # still on the phone step
    assert (await the_user(7)).phone == "+998900000001"


async def test_start_again_after_registering_greets_by_name(chat):
    await add_user(7, "Aziz Karimov")  # language: English
    await chat.say(7, "/start", **PROFILE)
    (greeting,) = chat.telegram.messages(7)
    assert "Hello, Aziz Karimov" in greeting["text"] and buttons_of(greeting)[0]["web_app"]  # ONB-8
    async with db.ReadSession() as s:
        assert len(list(await s.scalars(select(User)))) == 1


async def test_start_in_the_middle_restarts_from_the_name(chat):
    await begin(chat, 7)
    await chat.say(7, "Wrong Person", **PROFILE)
    assert "phone number" in chat.telegram.last(7)["text"]
    await chat.say(7, "/start", **PROFILE)  # ONB-9
    assert "first and last name" in chat.telegram.last(7)["text"]
    await chat.say(7, "Aziz Karimov", **PROFILE)
    await chat.say(7, "901234567", **PROFILE)
    assert (await the_user(7)).full_name == "Aziz Karimov"


async def test_language_follows_telegram_and_can_be_changed(chat):
    await chat.say(7, "/start", first_name="Азиз", last_name="Каримов", language_code="ru")  # NFR-15
    assert "имя и фамилию" in chat.telegram.last(7)["text"]
    await chat.say(8, "/start", first_name="Aziz", last_name="Karimov", language_code="de")
    assert "ism va familiyangizni" in chat.telegram.last(8)["text"]  # Uzbek unless Telegram is Russian
    await chat.tap(8, "lang:en", first_name="Aziz", last_name="Karimov", language_code="de")
    assert "first and last name" in chat.telegram.last(8)["text"]
    await chat.say(8, "Aziz Karimov", language_code="de")
    await chat.say(8, "901234567", language_code="de")
    assert (await the_user(8)).language == "en"


async def test_language_change_after_registering_is_remembered(chat, client):
    user = await add_user(7, "Aziz Karimov", language="uz")
    await chat.tap(7, "lang:ru")
    assert (await the_user(7)).language == "ru"  # APP-8: one choice for the bot and the Web App
    r = await client.post("/api/me/language", json={"language": "en"}, headers=auth(user))
    assert r.status_code == 200 and (await the_user(7)).language == "en"
    r = await client.post("/api/me/language", json={"language": "de"}, headers=auth(user))
    assert r.status_code == 400


async def test_unregistered_person_is_sent_to_start(chat, client):
    await chat.say(7, "hello", **PROFILE)
    assert "/start" in chat.telegram.last(7)["text"]
    r = await client.get("/api/me", headers=auth(7))  # ONB-11
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_registered"


async def test_blocked_person_is_told_access_is_disabled(chat, client):
    await add_user(7, "Aziz Karimov", is_blocked=True)
    await chat.say(7, "/start", **PROFILE)
    (only,) = chat.telegram.messages(7)
    assert "access has been disabled" in only["text"] and not buttons_of(only)  # ROLE-6
    r = await client.get("/api/me", headers=auth(7))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "blocked"


async def test_one_telegram_account_is_one_user(chat):
    await begin(chat, 7)
    await chat.say(7, "Aziz Karimov", **PROFILE)
    await chat.say(7, "901234567", **PROFILE)
    r = await chat.backend.register(telegram_id=7, username=None, full_name="Someone Else", phone="+998900000009",
                                    phone_verified=False, language="uz")
    assert r["user"]["name"] == "Aziz Karimov"  # ONB-8: registering again changes nothing
    async with db.ReadSession() as s:
        assert len(list(await s.scalars(select(User)))) == 1


async def test_the_bot_api_needs_the_bot_key(client):
    r = await client.post("/internal/users/lookup", json={"telegram_id": 7})
    assert r.status_code == 401
    r = await client.post("/internal/users/lookup", json={"telegram_id": 7}, headers={"X-Bot-Key": "wrong"})
    assert r.status_code == 401
