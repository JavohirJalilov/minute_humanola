"""Section 10: invite codes, the pay rate, the UseMinute connection and the change history."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend import db
from backend.models import InviteCode, Rate
from tests.conftest import add_code, add_rate, add_recording, add_user, assign, auth, link, outbox_rows

CAM = "cam-01@example.com"


async def useminute(client, who) -> dict:
    r = await client.get("/api/useminute", headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


async def page(client, who) -> dict:
    r = await client.get("/api/settings", headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


# ---- invite codes


async def test_adding_a_code_by_typing_it_or_generating_one(client, team):
    r = await client.post("/api/settings/invite-codes", json={"code": " 87gsgm84 ", "label": "Chilonzor  team", "staff_id": team.staff.id},
                          headers=auth(team.admin))
    assert r.status_code == 200 and r.json()["code"] == "87GSGM84"  # SET-2
    r = await client.post("/api/settings/invite-codes", json={"label": "Generated"}, headers=auth(team.admin))
    generated = r.json()["code"]
    assert len(generated) == 8 and generated.isalnum() and generated == generated.upper() and generated.isascii()

    codes = {c["code"]: c for c in (await page(client, team.admin))["invite_codes"]}
    typed = codes["87GSGM84"]  # SET-1
    assert typed["label"] == "Chilonzor team" and typed["staff"] == {"id": team.staff.id, "name": "Sam Staff"}
    assert typed["is_active"] is True and typed["created_by"] == "Ada Admin" and typed["created_at"] and typed["uses"] == 0
    assert codes[generated]["staff"] is None


@pytest.mark.parametrize("code", ["SHORT", "TOOLONG123", "87GSGM8!", "87GSGM 4", "АБВГДЕЖЗ"])
async def test_a_code_is_8_latin_letters_and_digits(client, team, code):
    r = await client.post("/api/settings/invite-codes", json={"code": code}, headers=auth(team.admin))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "code_format"


async def test_codes_are_unique(client, team):
    await add_code("87GSGM84")
    r = await client.post("/api/settings/invite-codes", json={"code": "87gsgm84"}, headers=auth(team.admin))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "code_exists"


async def test_a_code_can_be_used_by_any_number_of_workers_while_active(client, team, mailbox):
    invite = await add_code()
    for worker in (team.bek, team.cem):
        assert (await client.post("/api/link/invite", json={"code": "87GSGM84"}, headers=auth(worker))).status_code == 200  # SET-3
    r = await client.patch(f"/api/settings/invite-codes/{invite.id}", json={"is_active": False}, headers=auth(team.admin))
    assert r.json()["is_active"] is False
    extra = await add_user(60, "Extra Worker")
    r = await client.post("/api/link/invite", json={"code": "87GSGM84"}, headers=auth(extra))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invite_wrong"  # SET-4: stops new workers


async def test_workers_already_linked_with_a_deactivated_code_stay_linked(client, team):
    invite = await add_code()
    await link(team.bek, "cam-02@example.com", invite.id)
    await client.patch(f"/api/settings/invite-codes/{invite.id}", json={"is_active": False}, headers=auth(team.admin))
    assert (await client.get("/api/link", headers=auth(team.bek))).json()["step"] == "linked"  # SET-4
    listed = next(c for c in (await page(client, team.admin))["invite_codes"] if c["id"] == invite.id)
    assert listed["uses"] == 1 and listed["is_active"] is False


async def test_a_used_code_cannot_be_deleted_only_deactivated(client, team):
    used, unused = await add_code("USED0001"), await add_code("FREE0001")
    await link(team.bek, "cam-02@example.com", used.id)
    r = await client.delete(f"/api/settings/invite-codes/{used.id}", headers=auth(team.admin))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "code_used"  # SET-5
    assert (await client.delete(f"/api/settings/invite-codes/{unused.id}", headers=auth(team.admin))).status_code == 200
    async with db.ReadSession() as s:
        assert [c.code for c in await s.scalars(select(InviteCode))] == ["USED0001"]
    # a code someone has entered but not finished linking with counts as used too
    pending = await add_code("PEND0001")
    await client.post("/api/link/invite", json={"code": "PEND0001"}, headers=auth(team.cem))
    assert (await client.delete(f"/api/settings/invite-codes/{pending.id}", headers=auth(team.admin))).status_code == 409


async def test_a_code_can_be_given_to_a_staff_member_or_taken_back(client, team):
    invite = await add_code()
    patch = lambda **body: client.patch(f"/api/settings/invite-codes/{invite.id}", json=body, headers=auth(team.admin))  # noqa: E731
    assert (await patch(staff_id=team.cem.id)).status_code == 400  # not a staff member
    assert (await patch(staff_id=team.staff2.id, label="Sara's team")).status_code == 200
    listed = (await page(client, team.admin))["invite_codes"][0]
    assert listed["staff"]["name"] == "Sara Staff" and listed["label"] == "Sara's team"
    assert (await patch(staff_id=None)).status_code == 200
    assert (await page(client, team.admin))["invite_codes"][0]["staff"] is None
    assert (await client.patch("/api/settings/invite-codes/999", json={"label": "x"}, headers=auth(team.admin))).status_code == 404


# ---- the pay rate


@pytest.mark.parametrize("amount", [0, -1, 10_000_001, None])
async def test_the_rate_is_a_whole_number_between_1_and_10_million(client, team, amount):
    r = await client.post("/api/settings/rates", json={"amount": amount}, headers=auth(team.admin))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "rate_invalid"  # SET-7
    assert (await client.post("/api/settings/rates", json={"amount": 20_000.5}, headers=auth(team.admin))).status_code == 422


async def test_a_new_rate_keeps_the_previous_ones(client, team, settings):
    today = settings.today()
    assert (await page(client, team.admin))["rates"]["company"] is None
    for amount, start in ((20_000, today - timedelta(days=30)), (25_000, today), (30_000, today + timedelta(days=10))):
        r = await client.post("/api/settings/rates", json={"amount": amount, "start_date": start.isoformat()}, headers=auth(team.admin))
        assert r.status_code == 200
    rates = (await page(client, team.admin))["rates"]
    assert rates["company"]["amount"] == 25_000 and rates["company"]["start_date"] == today.isoformat()  # in force today
    assert [r["amount"] for r in rates["upcoming"]] == [30_000]
    assert [r["amount"] for r in rates["history"]] == [30_000, 25_000, 20_000]  # SET-8: nothing is overwritten
    assert all(r["created_by"] == "Ada Admin" for r in rates["history"])
    async with db.ReadSession() as s:
        assert len(list(await s.scalars(select(Rate)))) == 3
    # 1 and 10 000 000 are the limits
    for amount in (1, 10_000_000):
        assert (await client.post("/api/settings/rates", json={"amount": amount}, headers=auth(team.admin))).status_code == 200


async def test_a_rate_without_a_start_date_starts_today(client, team, settings):
    await client.post("/api/settings/rates", json={"amount": 20_000}, headers=auth(team.admin))
    assert (await page(client, team.admin))["rates"]["company"]["start_date"] == settings.today().isoformat()


async def test_a_staff_member_or_a_worker_can_have_their_own_rate(client, team, settings):
    await add_rate(20_000)
    await add_recording(CAM, 60, day=settings.today())
    post = lambda **body: client.post("/api/settings/rates", json=body, headers=auth(team.admin))  # noqa: E731
    assert (await post(scope="staff", user_id=team.cem.id, amount=25_000)).status_code == 400  # not a staff member
    assert (await post(scope="worker", user_id=4242, amount=25_000)).status_code == 404
    assert (await post(scope="team", amount=25_000)).status_code == 400
    assert (await post(scope="staff", user_id=team.staff.id, amount=25_000)).status_code == 200
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["earned"] == 25_000  # SET-9
    assert (await post(scope="worker", user_id=team.ali.id, amount=30_000)).status_code == 200
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["earned"] == 30_000
    overrides = (await page(client, team.admin))["rates"]["overrides"]
    assert [(o["scope"], o["user"]["name"], o["amount"]) for o in overrides] == [("staff", "Sam Staff", 25_000), ("worker", "Ali Worker", 30_000)]
    # ending the worker's own rate brings the staff member's back
    assert (await post(scope="worker", user_id=team.ali.id, amount=None)).status_code == 200
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["earned"] == 25_000
    assert [o["scope"] for o in (await page(client, team.admin))["rates"]["overrides"]] == ["staff"]
    r = await post(scope="worker", user_id=team.bek.id, amount=None)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rate_nothing_to_end"


async def test_a_rate_change_is_announced_to_the_workers_it_affects(client, team, settings):
    await link(team.bek, "cam-02@example.com")
    await assign(team.bek, team.staff2)
    today = settings.today().isoformat()
    r = await client.post("/api/settings/rates", json={"amount": 20_000, "start_date": today}, headers=auth(team.admin))
    assert r.json()["affected"] == 2  # SET-11: the linked workers; Cem has no phone
    told = {m.chat_id: m.text for m in await outbox_rows("rate.changed")}
    assert set(told) == {team.ali.telegram_id, team.bek.telegram_id}
    assert "20 000" in told[team.ali.telegram_id] and settings.today().strftime("%d.%m.%Y") in told[team.ali.telegram_id]

    # a rate for one staff member reaches only that staff member's workers
    await client.post("/api/settings/rates", json={"scope": "staff", "user_id": team.staff2.id, "amount": 22_000}, headers=auth(team.admin))
    assert [m.chat_id for m in (await outbox_rows("rate.changed"))[2:]] == [team.bek.telegram_id]
    # saving the same amount again changes nobody's rate, so nobody is told
    r = await client.post("/api/settings/rates", json={"amount": 20_000, "start_date": today}, headers=auth(team.admin))
    assert r.json()["affected"] == 0 and len(await outbox_rows("rate.changed")) == 3


# ---- the UseMinute connection and the change history


async def test_the_useminute_section_shows_the_last_sync(client, team, settings):
    from backend.services import doppi

    body = (await useminute(client, team.admin))["sync"]
    assert body["configured"] is False and body["last_sync_at"] is None and body["running"] is False
    await add_recording("stranger@example.com", 10, phone_model="iPhone 13")
    await add_recording(CAM, 10)
    await doppi.store([])  # nothing new; phones are refreshed by a real sync, so add them as one would
    async with db.WriteSession() as s:
        from backend.models import Phone

        s.add(Phone(email="stranger@example.com", model="iPhone 13", recordings=1))
        await s.commit()
    await doppi._save(finished_at=None, error="The UseMinute login has expired: put a new DOPPI_REFRESH_TOKEN in the server configuration and restart")
    body = (await useminute(client, team.admin))["sync"]
    assert body["ok"] is False and "login has expired" in body["error"]  # SET-12


async def test_the_doppi_login_is_never_shown(client, team, settings, monkeypatch):
    monkeypatch.setattr(settings, "doppi_api_key", "AIza-secret-key")
    monkeypatch.setattr(settings, "doppi_refresh_token", "refresh-secret-token")
    monkeypatch.setattr(settings, "doppi_org", "org-secret")
    text = (await client.get("/api/settings", headers=auth(team.admin))).text + (await client.get("/api/useminute", headers=auth(team.admin))).text
    assert (await useminute(client, team.admin))["sync"]["configured"] is True
    for secret in ("AIza-secret-key", "refresh-secret-token", "org-secret", settings.bot_token):
        assert secret not in text  # SET-15, NFR-4


async def test_sync_now_starts_a_sync_and_a_second_press_is_refused(client, team, settings, monkeypatch):
    import asyncio

    from backend.services import doppi

    monkeypatch.setattr(type(settings), "doppi_configured", property(lambda self: True))
    release = asyncio.Event()

    async def slow_report(settings, days, beat=None):
        await release.wait()
        return []

    async def no_accounts(settings, beat=None):
        return []

    monkeypatch.setattr(doppi, "fetch_report", slow_report)
    monkeypatch.setattr(doppi, "fetch_accounts", no_accounts)
    r = await client.post("/api/settings/sync", json={}, headers=auth(team.admin))
    assert r.status_code == 200 and r.json() == {"started": True}  # SET-13
    await asyncio.sleep(0.05)
    assert (await useminute(client, team.admin))["sync"]["running"] is True
    r = await client.post("/api/settings/sync", json={}, headers=auth(team.admin))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sync_running"
    release.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not (await useminute(client, team.admin))["sync"]["running"]:
            break
    body = (await useminute(client, team.admin))["sync"]
    assert body["running"] is False and body["ok"] is True and body["last_success_at"] is not None


async def test_every_settings_change_is_listed_with_who_and_when(client, team):
    invite = (await client.post("/api/settings/invite-codes", json={"code": "87GSGM84", "label": "A"}, headers=auth(team.admin))).json()
    await client.patch(f"/api/settings/invite-codes/{invite['id']}", json={"is_active": False}, headers=auth(team.admin2))
    await client.patch(f"/api/settings/invite-codes/{invite['id']}", json={"label": "B"}, headers=auth(team.boss))
    await client.post("/api/settings/rates", json={"amount": 20_000}, headers=auth(team.admin))
    await client.post("/api/settings/rates", json={"scope": "worker", "user_id": team.ali.id, "amount": 30_000}, headers=auth(team.admin))
    await client.delete(f"/api/settings/invite-codes/{invite['id']}", headers=auth(team.admin))
    history = (await page(client, team.admin))["history"]  # SET-16, newest first
    assert [(h["action"], h["by"]["name"]) for h in history] == [
        ("settings.invite_code.deleted", "Ada Admin"),
        ("settings.rate.added", "Ada Admin"),
        ("settings.rate.added", "Ada Admin"),
        ("settings.invite_code.changed", "Boss Super"),
        ("settings.invite_code.deactivated", "Adam Admin"),
        ("settings.invite_code.created", "Ada Admin"),
    ]
    assert all(h["at"] for h in history)
    assert history[1]["about"]["name"] == "Ali Worker" and history[1]["details"]["amount"] == 30_000
