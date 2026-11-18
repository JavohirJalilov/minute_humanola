"""The UseMinute section: the account list from UseMinute, linking only real accounts, and watching a
rejected recording's video."""

from datetime import datetime, timezone

import pytest

from backend import db
from backend.models import DoppiAccount, Recording
from backend.services import doppi
from tests.conftest import add_code, add_recording, auth, link


def account(email, disabled=False, **more):
    return {"email": email, "resourceKey": "key-" + email.split("@")[0], "disabled": disabled,
            "userState": "inactive" if disabled else "active", "joinedAt": "2026-11-01T10:00:00Z",
            "lastUploadCreatedAt": "2026-11-17T10:00:00Z", "uploadCount": 4, "totalDurationMs": 7_200_000, **more}


@pytest.fixture
def remote(monkeypatch, settings):
    class Remote:
        accounts: list = []
        files: list = []
        asked: list = []

    async def fetch_accounts(settings, beat=None):
        return list(Remote.accounts)

    async def fetch_report(settings, days, beat=None):
        return []

    async def fetch_reason_texts(settings, todo):
        return {}

    async def token(http, settings):
        return "id-token"

    async def get(http, url, params, token):
        Remote.asked.append(url)
        return {"files": Remote.files}

    Remote.accounts, Remote.files, Remote.asked = [], [], []
    monkeypatch.setattr(doppi, "fetch_accounts", fetch_accounts)
    monkeypatch.setattr(doppi, "fetch_report", fetch_report)
    monkeypatch.setattr(doppi, "fetch_reason_texts", fetch_reason_texts)
    monkeypatch.setattr(doppi, "_token", token)
    monkeypatch.setattr(doppi, "_get", get)
    monkeypatch.setattr(type(settings), "doppi_configured", property(lambda self: True))
    return Remote


async def accounts_page(client, who, **params) -> dict:
    r = await client.get("/api/useminute", params=params, headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


# ---- the account list


def test_an_account_from_the_users_list():
    a = doppi.normalize_account(account("Cam-07@Example.com"))
    assert a["email"] == "cam-07@example.com" and a["active"] is True and a["uploads"] == 4 and a["duration_ms"] == 7_200_000
    assert doppi.normalize_account(account("off@example.com", disabled=True))["active"] is False
    assert doppi.normalize_account({"email": ""}) is None


async def test_a_sync_brings_every_account_and_switches_off_those_no_longer_listed(remote, settings):
    remote.accounts = [account("a@example.com"), account("b@example.com"), account("c@example.com", disabled=True)]
    stats = await doppi.run(settings)
    assert stats.accounts == 2
    remote.accounts = [account("a@example.com")]
    await doppi.run(settings)
    async with db.ReadSession() as s:
        active = {a.email: a.active for a in await s.scalars(__import__("sqlalchemy").select(DoppiAccount))}
    assert active == {"a@example.com": True, "b@example.com": False, "c@example.com": False}


async def test_an_empty_answer_does_not_switch_every_account_off(remote, settings):
    remote.accounts = [account("a@example.com")]
    await doppi.run(settings)
    remote.accounts = []
    await doppi.run(settings)
    async with db.ReadSession() as s:
        assert (await s.get(DoppiAccount, "a@example.com")).active is True


async def test_admins_see_each_account_with_the_worker_who_linked_it(client, remote, settings, team):
    remote.accounts = [account("cam-01@example.com"), account("new@example.com"), account("gone@example.com", disabled=True)]
    await doppi.run(settings)
    d = await accounts_page(client, team.admin)
    assert d["counts"] == {"all": 2, "linked": 1, "not_linked": 1, "off": 1}
    # nobody has linked "new" yet: it comes first; switched-off accounts only show under their filter
    assert [(r["email"], r["user"]["name"] if r["user"] else None) for r in d["rows"]] == [
        ("new@example.com", None), ("cam-01@example.com", "Ali Worker")]
    assert [r["email"] for r in (await accounts_page(client, team.admin, filter="off"))["rows"]] == ["gone@example.com"]
    assert [r["email"] for r in (await accounts_page(client, team.admin, q="ali"))["rows"]] == ["cam-01@example.com"]
    assert d["sync"]["ok"] is True and d["sync"]["stats"]["accounts"] == 2


async def test_only_admins_open_the_useminute_section(client, team):
    for who in (team.ali, team.staff):
        assert (await client.get("/api/useminute", headers=auth(who))).status_code == 403
    me = (await client.get("/api/me", headers=auth(team.admin))).json()
    assert "useminute" in me["sections"]
    assert "useminute" not in (await client.get("/api/me", headers=auth(team.staff))).json()["sections"]


# ---- linking only a real account


async def to_email_step(client, user):
    await add_code("87GSGM84")
    r = await client.post("/api/link/invite", json={"code": "87gsgm84"}, headers=auth(user))
    assert r.json()["step"] == "email"


async def test_an_email_that_is_no_useminute_account_gets_no_code(client, remote, settings, team, mailbox):
    remote.accounts = [account("real@example.com"), account("off@example.com", disabled=True)]
    await doppi.run(settings)
    await to_email_step(client, team.bek)
    r = await client.post("/api/link/email", json={"email": "typo@example.com"}, headers=auth(team.bek))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "email_unknown" and not mailbox.sent
    r = await client.post("/api/link/email", json={"email": "off@example.com"}, headers=auth(team.bek))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "account_off" and not mailbox.sent
    r = await client.post("/api/link/email", json={"email": "Real@Example.com"}, headers=auth(team.bek))
    assert r.json()["step"] == "code" and mailbox.sent
    r = await client.post("/api/link/verify", json={"code": mailbox.code}, headers=auth(team.bek))
    assert r.json()["step"] == "linked"
    rows = (await accounts_page(client, team.admin, filter="linked"))["rows"]
    assert {r["email"]: r["user"]["name"] for r in rows}["real@example.com"] == "Bek Worker"


async def test_before_the_first_sync_any_email_can_be_linked(client, team, mailbox):
    await to_email_step(client, team.bek)
    r = await client.post("/api/link/email", json={"email": "anyone@example.com"}, headers=auth(team.bek))
    assert r.json()["step"] == "code"


async def test_contact_us_leads_to_the_support_chat(client, team, settings, monkeypatch):
    monkeypatch.setattr(settings, "support_contact", "@doppi_help")
    assert (await client.get("/api/me", headers=auth(team.bek))).json()["support"] == "https://t.me/doppi_help"
    monkeypatch.setattr(settings, "support_contact", "")
    assert (await client.get("/api/me", headers=auth(team.bek))).json()["support"] is None  # nobody has a username


# ---- feedback and videos


async def test_a_rejection_shows_the_verdict_and_how_many_videos_were_flagged(client, team):
    rid = await add_recording("cam-01@example.com", 10, "rejected", verdict="fraud", flagged_videos=2, chunks=5,
                              rejection_reason="Blurry: camera out of focus\nDark: no light")
    r = (await client.get("/api/recordings", headers=auth(team.ali))).json()["recordings"][0]
    assert (r["id"], r["verdict"], r["flagged_videos"], r["videos"]) == (rid, "fraud", 2, 5)
    assert r["reason"].split("\n") == ["Blurry: camera out of focus", "Dark: no light"]


async def test_the_worker_their_staff_member_and_admins_can_watch_a_video(client, remote, team):
    rid = await add_recording("cam-01@example.com", 10, "rejected")
    remote.files = [{"url": "https://v/ok.mp4"}, {"url": "https://v/bad.mp4", "flag": {"title": "Blurry", "description": "out of focus"}}]
    r = await client.get(f"/api/recordings/{rid}/video", headers=auth(team.ali))
    assert r.json()["files"] == [{"url": "https://v/bad.mp4", "flagged": True, "reason": "Blurry: out of focus"},
                                 {"url": "https://v/ok.mp4", "flagged": False, "reason": None}]
    assert remote.asked[-1].endswith(f"/users/u1/sessions/{rid}")
    for who in (team.staff, team.admin):
        assert (await client.get(f"/api/recordings/{rid}/video", params={"user_id": team.ali.id}, headers=auth(who))).status_code == 200
    # someone else's recording, even with a guessed id
    assert (await client.get(f"/api/recordings/{rid}/video", headers=auth(team.bek))).status_code == 404
    assert (await client.get(f"/api/recordings/{rid}/video", params={"user_id": team.ali.id}, headers=auth(team.staff2))).status_code == 403


async def test_a_recording_without_videos_says_so(client, remote, team):
    rid = await add_recording("cam-01@example.com", 10, "rejected")
    r = await client.get(f"/api/recordings/{rid}/video", headers=auth(team.ali))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "video_missing"


async def test_recordings_come_day_by_day_with_each_days_split(client, team):
    from datetime import timedelta

    from tests.conftest import TODAY, add_rate

    await add_rate(36_000)  # 600 so'm a minute
    yesterday = TODAY - timedelta(days=1)
    await add_recording("cam-01@example.com", 30, "accepted")
    await add_recording("cam-01@example.com", 10, "rejected")
    await add_recording("cam-01@example.com", 20, "pending", day=yesterday)
    await add_recording("someone@example.com", 99, "accepted")  # not Ali's phone
    d = (await client.get("/api/recordings/days", headers=auth(team.ali))).json()
    assert [x["day"] for x in d["days"]] == [TODAY.isoformat(), yesterday.isoformat()] and d["has_more"] is False
    today = d["days"][0]
    assert (today["minutes"], today["recordings"]) == (40, 2)
    assert (today["accepted"]["minutes"], today["accepted"]["amount"], today["rejected"]["recordings"]) == (30, 18_000, 1)
    page = (await client.get("/api/recordings/days", params={"limit": 1}, headers=auth(team.ali))).json()
    assert len(page["days"]) == 1 and page["has_more"] is True
    assert (await client.get("/api/recordings/days", params={"user_id": team.ali.id}, headers=auth(team.bek))).status_code == 403
