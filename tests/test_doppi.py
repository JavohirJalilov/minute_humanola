"""Section 13.2: reading recording data from UseMinute (EXT-3..12), with UseMinute itself replaced by fixed answers."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from backend import clock, db, jobs
from backend.models import Phone, Recording, SyncState
from backend.services import doppi, notify
from tests.conftest import add_rate, add_user, assign, auth, link, outbox_rows

TZ = ZoneInfo("Asia/Tashkent")
CAM = "cam-01@example.com"


def upload(session_id="s1", email=CAM, status="accepted", minutes=30.0, recorded="2026-11-10T05:00:00Z", uploaded="2026-11-10T09:00:00Z", **more):
    return {"sessionId": session_id, "userResourceKey": "user-1", "email": email, "reviewStatus": status, "minutesCollected": minutes,
            "recordedAt": recorded, "uploadedAt": uploaded, "phoneModel": "iPhone 13", "deviceType": "ios", "taskName": "Cooking",
            "taskCategory": "Kitchen", "rateMultiplier": 1.5, "chunkCount": 3, **more}


@pytest.fixture
def remote(monkeypatch, settings):
    """UseMinute's answers: `remote.rows` is the upload report, `remote.reasons` the reviewers' reasons,
    `remote.accounts` the organisation's users list."""
    class Remote:
        rows: list = []
        accounts: list = []
        reasons: dict = {}
        error: Exception | None = None
        windows: list = []

    async def fetch_report(settings, days, beat=None):
        Remote.windows.append(days)
        if Remote.error:
            raise Remote.error
        return list(Remote.rows)

    async def fetch_accounts(settings, beat=None):
        if Remote.error:
            raise Remote.error
        return list(Remote.accounts)

    async def fetch_reason_texts(settings, todo):
        return {rid: Remote.reasons.get(rid, "") for rid, _ in todo}

    Remote.rows, Remote.reasons, Remote.error, Remote.windows, Remote.accounts = [], {}, None, [], []
    monkeypatch.setattr(doppi, "fetch_accounts", fetch_accounts)
    monkeypatch.setattr(doppi, "fetch_report", fetch_report)
    monkeypatch.setattr(doppi, "fetch_reason_texts", fetch_reason_texts)
    monkeypatch.setattr(type(settings), "doppi_configured", property(lambda self: True))
    monkeypatch.setattr(settings, "doppi_refresh_token", "token-1")
    return Remote


async def recordings() -> dict[str, Recording]:
    async with db.ReadSession() as s:
        return {r.id: r for r in await s.scalars(select(Recording))}


async def sync_state() -> SyncState:
    async with db.ReadSession() as s:
        return await s.get(SyncState, 1)


# ---- reading one row


def test_a_row_becomes_a_recording():
    r = doppi.normalize(upload(email="  CAM-01@Example.com ", minutes=12.3456), TZ)
    assert r["id"] == "s1" and r["email"] == CAM and r["user_key"] == "user-1"  # EXT-7: lower case
    assert r["minutes"] == 12.3456 and r["duration_ms"] == 740_736
    assert (r["phone_model"], r["task_name"], r["category"], r["rate_multiplier"], r["chunks"]) == ("iPhone 13", "Cooking", "Kitchen", 1.5, 3)
    assert r["recorded_at"] == datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc) and r["clock_fixed"] is False


@pytest.mark.parametrize("doppi_status, shown", [
    ("accepted", "accepted"), ("pending", "pending"), ("flagged", "rejected"), ("rejected", "rejected"),
    ("FLAGGED", "rejected"), ("in_review", "pending"), ("", "pending"), (None, "pending"),
])
def test_review_statuses(doppi_status, shown):
    assert doppi.normalize(upload(status=doppi_status), TZ)["status"] == shown  # EXT-10


@pytest.mark.parametrize("recorded, day", [
    ("2026-11-15T18:59:59Z", date(2026, 11, 15)),  # 23:59:59 in Tashkent
    ("2026-11-15T19:00:00Z", date(2026, 11, 16)),  # midnight in Tashkent
    ("2026-11-16T02:00:00+05:00", date(2026, 11, 16)),
])
def test_a_day_is_the_tashkent_calendar_day_of_the_recording(recorded, day):
    assert doppi.normalize(upload(recorded=recorded, uploaded="2026-11-17T00:00:00Z"), TZ)["day"] == day  # EXT-8, PRG-9


def test_a_clock_12_hours_ahead_is_moved_back():
    # "recorded" 17:00, 30 minutes long, but uploaded at 05:40: it can't have ended after it was uploaded (EXT-9)
    r = doppi.normalize(upload(recorded="2026-11-10T17:00:00Z", uploaded="2026-11-10T05:40:00Z", minutes=30), TZ)
    assert r["recorded_at"] == datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc) and r["clock_fixed"] is True
    assert r["day"] == date(2026, 11, 10)
    ok = doppi.normalize(upload(recorded="2026-11-10T05:00:00Z", uploaded="2026-11-10T05:31:00Z", minutes=30), TZ)
    assert ok["clock_fixed"] is False
    no_upload_time = doppi.normalize(upload(uploaded=None), TZ)
    assert no_upload_time["clock_fixed"] is False


@pytest.mark.parametrize("broken", [
    {"sessionId": ""}, {"email": ""}, {"recordedAt": "yesterday"}, {"recordedAt": None}, {"minutesCollected": "a lot"},
    {"minutesCollected": -5},
])
def test_rows_that_cannot_be_used_are_skipped(broken):
    assert doppi.normalize(upload(**broken), TZ) is None


def test_the_reason_of_a_rejection():
    detail = {"files": [
        {"flag": {"title": "Hands not visible", "description": "The hands leave the frame"}},
        {"flag": {"title": "Hands not visible", "description": "The hands leave the frame"}},
        {"flag": {"title": "Too dark"}}, {"flag": None}, {},
    ]}
    assert doppi.reason_of(detail) == "Hands not visible: The hands leave the frame\nToo dark"
    assert doppi.reason_of({}) == ""


# ---- a sync


async def test_a_sync_adds_new_recordings_and_updates_known_ones(remote, settings, team):
    remote.rows = [upload("s1", status="pending"), upload("s2", status="accepted", minutes=45), upload("s3", email="Stranger@example.com")]
    stats = await jobs.sync_and_notify(settings)
    assert (stats.recordings, stats.new, stats.phones) == (3, 3, 2)
    rows = await recordings()
    assert rows["s1"].status == "pending" and rows["s3"].email == "stranger@example.com"  # EXT-12: kept though nobody linked it
    first_seen = rows["s1"].first_seen_at

    clock.shift(timedelta(hours=6))
    remote.rows = [upload("s1", status="accepted"), upload("s2", status="flagged", minutes=45), upload("s4")]
    remote.reasons = {"s2": "Too dark: Nothing can be seen"}
    stats = await jobs.sync_and_notify(settings)
    assert (stats.recordings, stats.new, stats.reasons) == (3, 1, 1)
    rows = await recordings()
    assert rows["s1"].status == "accepted" and rows["s1"].first_seen_at == first_seen and rows["s1"].synced_at > first_seen  # EXT-4
    assert (rows["s2"].status, rows["s2"].review_status, rows["s2"].rejection_reason) == ("rejected", "flagged", "Too dark: Nothing can be seen")
    assert set(rows) == {"s1", "s2", "s3", "s4"}  # s3 was not in this report: it stays

    async with db.ReadSession() as s:
        phone = await s.get(Phone, CAM)
    assert (phone.model, phone.recordings) == ("iPhone 13", 3) and phone.first_recorded_at is not None
    state = await sync_state()
    assert state.error is None and state.success_at is not None and state.stats["recordings"] == 3 and state.lock_token is None


async def test_the_first_sync_reads_the_history_later_ones_the_last_30_days(remote, settings):
    await jobs.sync_and_notify(settings)
    await jobs.sync_and_notify(settings)
    assert remote.windows == [settings.doppi_initial_days, settings.doppi_sync_days] == [365, 30]  # EXT-4
    assert settings.doppi_sync_days >= 30


async def test_a_recording_that_is_no_longer_rejected_loses_its_reason(remote, settings, team):
    remote.rows, remote.reasons = [upload("s1", status="flagged")], {"s1": "Too dark"}
    await jobs.sync_and_notify(settings)
    remote.rows = [upload("s1", status="accepted")]
    await jobs.sync_and_notify(settings)
    row = (await recordings())["s1"]
    assert row.status == "accepted" and row.rejection_reason is None and row.rejection_notified_at is None


async def test_screens_read_the_stored_copy_and_say_when_it_was_updated(remote, settings, team, client):
    await add_rate(60_000)
    remote.rows = [upload("s1", minutes=60)]
    await jobs.sync_and_notify(settings)
    synced = (await sync_state()).success_at
    remote.error = doppi.DoppiError("UseMinute did not answer")  # UseMinute is down
    with pytest.raises(doppi.DoppiError):
        await jobs.sync_and_notify(settings)
    for path in ("/api/progress", "/api/payments/me", "/api/recordings"):
        body = (await client.get(path, headers=auth(team.ali))).json()  # EXT-6: the last synced data stays visible
        assert datetime.fromisoformat(body["synced_at"]) == synced  # APP-7, PRG-8
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["earned"] == 60_000
    for path, who in (("/api/staff/workers", team.staff), ("/api/admin/staff", team.admin), ("/api/users", team.admin)):
        assert (await client.get(path, headers=auth(who))).json()["synced_at"] is not None


async def test_a_failed_sync_is_recorded_and_the_super_admins_are_told_once(remote, settings, team):
    remote.error = doppi.DoppiError("UseMinute did not answer")
    for _ in range(3):
        with pytest.raises(doppi.DoppiError):
            await jobs.sync_and_notify(settings)
    state = await sync_state()
    assert state.error == "UseMinute did not answer" and state.success_at is None and state.lock_token is None
    alerts = await outbox_rows("sync.failed")
    assert [m.chat_id for m in alerts] == [team.boss.telegram_id] and "UseMinute did not answer" in alerts[0].text
    clock.shift(timedelta(hours=25))  # still failing a day later: told again
    with pytest.raises(doppi.DoppiError):
        await jobs.sync_and_notify(settings)
    assert len(await outbox_rows("sync.failed")) == 2
    remote.error = RuntimeError("boom")  # an unexpected failure is recorded too, without details that could hold data
    with pytest.raises(doppi.DoppiError):
        await jobs.sync_and_notify(settings)
    assert (await sync_state()).error == "UseMinute sync failed: RuntimeError"


async def test_a_refused_login_stops_the_schedule_until_the_login_changes(remote, settings, team, monkeypatch):
    monkeypatch.setattr(settings, "doppi_sync_times", "00:00")
    remote.error = doppi.LoginRefused("The UseMinute login has expired: put a new DOPPI_REFRESH_TOKEN in the server configuration and restart")
    assert await doppi.due(settings) is True
    with pytest.raises(doppi.LoginRefused):
        await jobs.sync_and_notify(settings)
    assert "login has expired" in (await outbox_rows("sync.failed"))[0].text  # EXT-11
    clock.shift(timedelta(days=2))
    assert await doppi.due(settings) is False  # syncing stops
    monkeypatch.setattr(settings, "doppi_refresh_token", "token-2")  # renewed by hand in the server configuration
    assert await doppi.due(settings) is True
    remote.error = None
    await jobs.sync_and_notify(settings)
    assert (await sync_state()).refused_login is None


async def test_one_sync_at_a_time(remote, settings, monkeypatch):
    gate = asyncio.Event()

    async def slow(settings, days, beat=None):
        await gate.wait()
        return []

    monkeypatch.setattr(doppi, "fetch_report", slow)
    first = asyncio.create_task(doppi.run(settings))
    await asyncio.sleep(0.05)
    async with db.ReadSession() as s:
        assert await doppi.is_running(s) is True
    with pytest.raises(doppi.SyncBusy):
        await doppi.run(settings)  # SET-13
    gate.set()
    await first
    async with db.ReadSession() as s:
        assert await doppi.is_running(s) is False
    await doppi.run(settings)


async def test_a_sync_that_died_does_not_block_the_next_one_forever(remote, settings):
    async with db.WriteSession() as s:  # a process was killed in the middle of a sync
        await s.execute(update(SyncState).values(lock_token="dead", lock_at=clock.now()))
        await s.commit()
    with pytest.raises(doppi.SyncBusy):
        await doppi.run(settings)
    clock.shift(doppi.LOCK_STALE + timedelta(seconds=1))
    await doppi.run(settings)


async def test_the_schedule(remote, settings, monkeypatch):
    monkeypatch.setattr(settings, "doppi_sync_times", "00:00, 12:00")
    assert await doppi.due(settings) is True  # never synced
    await doppi.run(settings)
    assert await doppi.due(settings) is False
    clock.shift(timedelta(hours=12, minutes=1))  # the next scheduled time has passed
    assert await doppi.due(settings) is True
    remote.error = doppi.DoppiError("UseMinute did not answer")
    with pytest.raises(doppi.DoppiError):
        await doppi.run(settings)
    assert await doppi.due(settings) is False  # a failed run is retried after an hour, not every minute
    clock.shift(timedelta(minutes=61))
    assert await doppi.due(settings) is True
    monkeypatch.setattr(settings, "doppi_sync_times", "")
    assert await doppi.due(settings) is False  # the schedule is off


async def test_without_a_login_the_sync_says_so(settings):
    with pytest.raises(doppi.DoppiError, match="not set up"):
        await doppi.run(settings)


# ---- telling workers about rejections


async def test_rejections_reach_the_worker_as_one_summary_a_day(remote, settings, team):
    remote.rows = [upload("s1", status="flagged", minutes=20, recorded="2026-11-10T05:00:00Z"),
                   upload("s2", status="flagged", minutes=95, recorded="2026-11-11T05:00:00Z"),
                   upload("s3", status="accepted"),
                   upload("s4", status="flagged", email="stranger@example.com")]
    remote.reasons = {"s1": "Too dark: Nothing can be seen\nSecond video: blurry", "s2": ""}
    await jobs.sync_and_notify(settings)
    (summary,) = await outbox_rows("recordings.rejected")
    assert summary.chat_id == team.ali.telegram_id
    assert "rejected 2 of your recordings" in summary.text and CAM in summary.text  # which phone
    assert "10.11.2026 · 0h 20m — Too dark: Nothing can be seen" in summary.text  # the date, the length, UseMinute's reason
    assert "11.11.2026 · 1h 35m" in summary.text and "Second video" not in summary.text

    remote.rows.append(upload("s5", status="flagged", recorded="2026-11-12T05:00:00Z"))
    await jobs.sync_and_notify(settings)
    assert len(await outbox_rows("recordings.rejected")) == 1  # at most one summary message per day
    clock.shift(timedelta(days=1))
    await jobs.sync_and_notify(settings)
    second = (await outbox_rows("recordings.rejected"))[1]
    assert "rejected 1 of your recordings" in second.text and "12.11.2026" in second.text
    clock.shift(timedelta(days=1))
    await jobs.sync_and_notify(settings)
    assert len(await outbox_rows("recordings.rejected")) == 2  # nothing new, nothing sent


async def test_a_long_list_of_rejections_is_shortened(remote, settings, team):
    remote.rows = [upload(f"s{i}", status="flagged", recorded=f"2026-11-{i + 1:02d}T05:00:00Z", uploaded="2026-11-20T00:00:00Z") for i in range(14)]
    await jobs.sync_and_notify(settings)
    (summary,) = await outbox_rows("recordings.rejected")
    assert "rejected 14 of your recordings" in summary.text and summary.text.count("•") == 10 and "…and 4 more" in summary.text


async def test_rejections_from_before_the_link_are_not_sent_to_the_chat(remote, settings, team, client, mailbox):
    from tests.conftest import add_code

    remote.rows = [upload("s1", status="flagged", email="cam-02@example.com")]
    await jobs.sync_and_notify(settings)
    await add_code()
    await client.post("/api/link/invite", json={"code": "87GSGM84"}, headers=auth(team.bek))
    await client.post("/api/link/email", json={"email": "cam-02@example.com"}, headers=auth(team.bek))
    clock.shift(timedelta(minutes=6))
    await client.post("/api/link/verify", json={"code": mailbox.code}, headers=auth(team.bek))
    assert await outbox_rows("recordings.rejected") == []
    assert "Recordings found: <b>1</b>" in (await outbox_rows("link.created"))[0].text
    remote.rows.append(upload("s2", status="flagged", email="cam-02@example.com"))  # a new one after linking
    clock.shift(timedelta(minutes=6))
    await jobs.sync_and_notify(settings)
    (summary,) = await outbox_rows("recordings.rejected")
    assert summary.chat_id == team.bek.telegram_id and "rejected 1 of your recordings" in summary.text
