"""Section 7.1: linking a UseMinute account (LNK-1..16), through the Web App API."""

from datetime import timedelta

from sqlalchemy import select

from backend import clock, db
from backend.models import DoppiLink, Phone, StaffAssignment, VerificationAttempt
from backend.services import mailer
from tests.conftest import add_code, add_recording, add_user, assign, auth, link, outbox_rows

EMAIL = "cam-07@example.com"


async def step(client, user) -> dict:
    r = await client.get("/api/link", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


async def post(client, user, path: str, **body):
    return await client.post(f"/api/link/{path}", json=body, headers=auth(user))


async def to_code_step(client, user, mailbox, email: str = EMAIL, code: str = "87GSGM84") -> str:
    assert (await post(client, user, "invite", code=code)).status_code == 200
    r = await post(client, user, "email", email=email)
    assert r.status_code == 200, r.text
    return mailbox.code


async def test_invite_code_then_email_then_code_links_the_phone(client, mailbox, team):
    invite = await add_code()
    assert (await step(client, team.bek))["step"] == "invite"

    r = await post(client, team.bek, "invite", code="  87gsgm84 ")  # LNK-1: case and spaces don't matter
    assert r.status_code == 200 and r.json() == {"step": "email", "invite_code": "87GSGM84"}  # LNK-16

    r = await post(client, team.bek, "email", email="  CAM-07@Example.com ")
    state = r.json()
    assert state["step"] == "code" and state["email"] == EMAIL and state["invite_code"] == "87GSGM84"  # LNK-4, LNK-16
    assert state["code_active"] is True
    assert mailbox.sent[-1]["to"] == EMAIL and len(mailbox.code) == 6 and mailbox.code.isdigit()  # LNK-6

    r = await post(client, team.bek, "verify", code=mailbox.code)
    assert r.status_code == 200 and r.json()["step"] == "linked" and r.json()["email"] == EMAIL

    async with db.ReadSession() as s:
        row = await s.scalar(select(DoppiLink).where(DoppiLink.user_id == team.bek.id))
        assert (row.email, row.invite_code_id, row.unlinked_at) == (EMAIL, invite.id, None)  # LNK-3: which code, and when
        assert row.verified_at is not None
        assert await s.get(Phone, EMAIL) is not None
    # It appears in My Progress as one phone, identified by that email (LNK-12: even with no recordings yet)
    r = await client.get("/api/progress", headers=auth(team.bek))
    assert [p["email"] for p in r.json()["phones"]] == [EMAIL] and r.json()["total"]["minutes"] == 0
    (note,) = await outbox_rows("link.created")
    assert EMAIL in note.text and "No recordings were found yet" in note.text


async def test_linked_notice_counts_recordings_already_stored(client, mailbox, team):
    await add_code()
    await add_recording(EMAIL, 30)  # EXT-12: stored before anyone linked the email
    await add_recording(EMAIL, 45, "rejected")
    code = await to_code_step(client, team.bek, mailbox)
    await post(client, team.bek, "verify", code=code)
    (note,) = await outbox_rows("link.created")
    assert "Recordings found: <b>2</b>" in note.text
    r = await client.get("/api/progress", headers=auth(team.bek))
    assert r.json()["total"]["minutes"] == 75


async def test_wrong_invite_codes_lock_the_form_for_15_minutes(client, team):
    await add_code()
    await add_code("OLDCODE1", active=False)
    for attempt, code in enumerate(["WRONG123", "short", "OLDCODE1", "87GSGM8!"], start=1):
        r = await post(client, team.bek, "invite", code=code)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invite_wrong"  # LNK-2, SET-4
        assert r.json()["detail"]["params"]["attempts_left"] == 5 - attempt
    r = await post(client, team.bek, "invite", code="WRONG999")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "invite_locked"
    r = await post(client, team.bek, "invite", code="87GSGM84")  # even the right code waits
    assert r.status_code == 429
    assert (await step(client, team.bek))["locked_until"] is not None

    clock.shift(timedelta(minutes=14))
    assert (await post(client, team.bek, "invite", code="87GSGM84")).status_code == 429
    clock.shift(timedelta(minutes=2))
    assert (await post(client, team.bek, "invite", code="87GSGM84")).status_code == 200


async def test_email_must_look_like_an_email(client, mailbox, team):
    await add_code()
    await post(client, team.bek, "invite", code="87GSGM84")
    for bad in ["", "cam-07", "cam@", "a b@example.com", "cam@example", "@example.com"]:
        r = await post(client, team.bek, "email", email=bad)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "email_invalid", bad
    assert mailbox.sent == [] and (await step(client, team.bek))["step"] == "email"


async def test_email_step_needs_the_invite_code_first(client, mailbox, team):
    r = await post(client, team.bek, "email", email=EMAIL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_required"


async def test_email_linked_to_another_user_is_refused(client, mailbox, team):
    await add_code()
    await post(client, team.bek, "invite", code="87GSGM84")
    r = await post(client, team.bek, "email", email="Cam-01@example.com")  # ali's
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_taken"  # LNK-5
    assert "administrator" in r.json()["detail"]["message"] and mailbox.sent == []


async def test_code_expires_after_10_minutes_and_works_once(client, mailbox, team):
    await add_code()
    code = await to_code_step(client, team.bek, mailbox)
    clock.shift(timedelta(minutes=10, seconds=1))
    r = await post(client, team.bek, "verify", code=code)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "code_expired"  # LNK-6
    assert (await step(client, team.bek))["code_active"] is False

    r = await post(client, team.bek, "resend")
    assert r.status_code == 200 and mailbox.code != code or len(mailbox.sent) == 2
    assert (await post(client, team.bek, "verify", code=mailbox.code)).status_code == 200
    # usable once: the same code does nothing for anyone afterwards
    r = await post(client, team.bek, "verify", code=mailbox.code)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_linked"


async def test_an_older_code_stops_working_when_a_new_one_is_sent(client, mailbox, team):
    await add_code()
    first = await to_code_step(client, team.bek, mailbox)
    clock.shift(timedelta(seconds=61))
    await post(client, team.bek, "resend")
    second = mailbox.code
    if first != second:
        assert (await post(client, team.bek, "verify", code=first)).status_code == 400
    assert (await post(client, team.bek, "verify", code=second)).status_code == 200


async def test_resend_waits_60_seconds_and_stops_at_5_an_hour(client, mailbox, team):
    await add_code()
    await to_code_step(client, team.bek, mailbox)
    r = await post(client, team.bek, "resend")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "resend_wait"  # LNK-7
    assert 0 < r.json()["detail"]["params"]["seconds"] <= 60
    for _ in range(4):
        clock.shift(timedelta(seconds=61))
        assert (await post(client, team.bek, "resend")).status_code == 200
    assert len(mailbox.sent) == 5
    clock.shift(timedelta(seconds=61))
    r = await post(client, team.bek, "resend")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "too_many_codes"
    clock.shift(timedelta(minutes=56))  # an hour after the first one
    assert (await post(client, team.bek, "resend")).status_code == 200


async def test_five_wrong_entries_cancel_the_code(client, mailbox, team):
    await add_code()
    code = await to_code_step(client, team.bek, mailbox)
    wrong = "000000" if code != "000000" else "111111"
    for attempt in range(1, 5):
        r = await post(client, team.bek, "verify", code=wrong)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "code_wrong"
        assert r.json()["detail"]["params"]["attempts_left"] == 5 - attempt
    r = await post(client, team.bek, "verify", code=wrong)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "code_cancelled"  # LNK-8
    r = await post(client, team.bek, "verify", code=code)  # the right code is dead too
    assert r.status_code == 410
    state = await step(client, team.bek)
    assert state["step"] == "code" and state["code_active"] is False
    clock.shift(timedelta(seconds=61))
    await post(client, team.bek, "resend")
    assert (await post(client, team.bek, "verify", code=mailbox.code)).status_code == 200


async def test_going_back_to_correct_a_mistyped_email(client, mailbox, team):
    await add_code()
    wrong_code = await to_code_step(client, team.bek, mailbox, email="cam-70@example.com")
    r = await post(client, team.bek, "change-email")
    assert r.json() == {"step": "email", "invite_code": "87GSGM84"}  # LNK-10
    clock.shift(timedelta(seconds=61))
    r = await post(client, team.bek, "email", email=EMAIL)
    assert r.json()["email"] == EMAIL
    assert (await post(client, team.bek, "verify", code=mailbox.code)).status_code == 200
    async with db.ReadSession() as s:
        assert (await s.scalar(select(DoppiLink.email).where(DoppiLink.user_id == team.bek.id))) == EMAIL
    assert wrong_code  # the first code went to the mistyped address and was never used


async def test_failed_email_shows_an_error_and_does_not_count(client, mailbox, team):
    await add_code()
    await post(client, team.bek, "invite", code="87GSGM84")
    mailbox.fail = True
    for _ in range(6):  # more than the hourly limit: failures are not counted (EXT-15)
        r = await post(client, team.bek, "email", email=EMAIL)
        assert r.status_code == 502 and r.json()["detail"]["code"] == "email_failed"
    assert (await step(client, team.bek))["step"] == "email"
    async with db.ReadSession() as s:
        assert list(await s.scalars(select(VerificationAttempt))) == []
    mailbox.fail = False
    assert (await post(client, team.bek, "email", email=EMAIL)).status_code == 200  # and the retry works at once


async def test_the_code_is_stored_hashed(client, mailbox, team):
    await add_code()
    code = await to_code_step(client, team.bek, mailbox)
    async with db.ReadSession() as s:
        attempt = await s.scalar(select(VerificationAttempt))
    assert code not in attempt.code_hash and len(attempt.code_hash) == 64  # NFR-5


def test_the_email_is_in_the_workers_language_and_names_the_bot(settings):
    for lang, word in (("uz", "kodingiz"), ("ru", "код"), ("en", "code")):
        message = mailer.build(settings, EMAIL, "419302", lang, "Humanola Pay", "humanola_pay_bot")  # LNK-9
        body = message.get_content()
        assert "419302" in body and "Humanola Pay (@humanola_pay_bot)" in body and word in body
        assert "Humanola Pay" in message["Subject"] and "419302" in message["Subject"]


async def test_one_worker_links_one_email(client, mailbox, team):
    await add_code()
    r = await post(client, team.ali, "invite", code="87GSGM84")  # ali already has cam-01
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_linked"  # LNK-13
    assert (await step(client, team.ali)) | {"linked_at": None} == {"step": "linked", "email": "cam-01@example.com", "linked_at": None}


async def test_a_code_deactivated_before_the_email_step_sends_the_worker_back(client, mailbox, team):
    invite = await add_code()
    await post(client, team.bek, "invite", code="87GSGM84")
    r = await client.patch(f"/api/settings/invite-codes/{invite.id}", json={"is_active": False}, headers=auth(team.admin))
    assert r.status_code == 200
    r = await post(client, team.bek, "email", email=EMAIL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_inactive"
    assert (await step(client, team.bek))["step"] == "invite"


async def test_a_staff_members_code_assigns_the_worker_to_them(client, mailbox, team):
    await add_code("STAFF001", staff=team.staff2)
    code = await to_code_step(client, team.bek, mailbox, code="STAFF001")
    await post(client, team.bek, "verify", code=code)
    async with db.ReadSession() as s:
        a = await s.scalar(select(StaffAssignment).where(StaffAssignment.worker_id == team.bek.id))
    assert (a.staff_id, a.source, a.set_by_id, a.ended_at) == (team.staff2.id, "invite_code", None, None)  # USR-11, SET-6
    told = {m.chat_id: m.text for m in await outbox_rows("staff.assigned")}
    assert "Sara Staff" in told[team.bek.telegram_id] and team.staff2.phone[-4:-2] in told[team.bek.telegram_id]
    assert "Bek Worker" in told[team.staff2.telegram_id]


async def test_a_code_does_not_override_an_admins_assignment_or_assign_to_oneself(client, mailbox, team):
    await add_code("STAFF001", staff=team.staff2)
    await assign(team.bek, team.staff)  # set by an Admin before linking
    code = await to_code_step(client, team.bek, mailbox, code="STAFF001")
    await post(client, team.bek, "verify", code=code)
    # the staff member linking with their own code doesn't become their own staff (USR-15)
    clock.shift(timedelta(seconds=61))
    code = await to_code_step(client, team.staff2, mailbox, email="cam-08@example.com", code="STAFF001")
    assert (await post(client, team.staff2, "verify", code=code)).status_code == 200
    async with db.ReadSession() as s:
        rows = list(await s.scalars(select(StaffAssignment).where(StaffAssignment.ended_at.is_(None))))
    assert {(a.worker_id, a.staff_id) for a in rows} == {(team.ali.id, team.staff.id), (team.bek.id, team.staff.id)}


async def test_linking_fetches_from_doppi_right_away(client, mailbox, team, settings, monkeypatch):
    from backend.services import doppi

    calls = []

    async def run(settings, days=None):
        calls.append(days)
        return doppi.Stats()

    monkeypatch.setattr(doppi, "run", run)
    monkeypatch.setattr(type(settings), "doppi_configured", property(lambda self: True))
    await add_code()
    code = await to_code_step(client, team.bek, mailbox)
    await post(client, team.bek, "verify", code=code)
    assert calls == [None]  # LNK-11: the worker doesn't wait for the scheduled sync


async def test_a_replaced_account_keeps_its_recordings_in_the_earnings(client, mailbox, team):
    """LNK-14, LNK-15: the Admin unlinks the old email, the worker links the new one."""
    from tests.conftest import add_rate

    await add_rate(60_000)
    await add_code()
    await add_recording("cam-01@example.com", 60)
    r = await client.post(f"/api/users/{team.ali.id}/unlink", json={}, headers=auth(team.ali))
    assert r.status_code == 403  # a worker cannot unlink an email themselves
    r = await client.post(f"/api/users/{team.ali.id}/unlink", json={}, headers=auth(team.admin))
    assert r.status_code == 200 and r.json()["email"] == "cam-01@example.com"
    assert (await step(client, team.ali))["step"] == "invite"

    code = await to_code_step(client, team.ali, mailbox, email="cam-01b@example.com")
    assert (await post(client, team.ali, "verify", code=code)).status_code == 200
    await add_recording("cam-01b@example.com", 30)
    r = await client.get("/api/payments/me", headers=auth(team.ali))
    body = r.json()
    assert body["summary"]["earned"] == 90_000  # 60 min under the old email + 30 under the new
    assert [(p["email"], p["current"]) for p in body["phones"]] == [("cam-01b@example.com", True), ("cam-01@example.com", False)]


async def test_an_email_linked_to_the_wrong_person_moves_with_its_recordings(client, mailbox, team):
    from tests.conftest import add_rate

    await add_rate(60_000)
    await add_code()
    await add_recording("cam-01@example.com", 60)
    r = await client.post(f"/api/users/{team.ali.id}/unlink", json={"keep_recordings": False}, headers=auth(team.admin))
    assert r.status_code == 200
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["earned"] in (0, None)
    code = await to_code_step(client, team.bek, mailbox, email="cam-01@example.com")
    assert (await post(client, team.bek, "verify", code=code)).status_code == 200
    assert (await client.get("/api/payments/me", headers=auth(team.bek))).json()["summary"]["earned"] == 60_000
