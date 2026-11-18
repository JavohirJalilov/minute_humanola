"""Section 6: the Users section — lists, profile, role changes, assigning workers to staff."""

from datetime import timedelta

from sqlalchemy import select

from backend import clock, db
from backend.models import AuditLog, InviteCode, StaffAssignment, User
from tests.conftest import add_code, add_rate, add_recording, add_user, assign, auth, fund, link, outbox_rows, pay

CAM = "cam-01@example.com"


async def users(client, who, **params) -> dict:
    r = await client.get("/api/users", params=params, headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


async def current_staff(worker) -> int | None:
    async with db.ReadSession() as s:
        return await s.scalar(select(StaffAssignment.staff_id).where(StaffAssignment.worker_id == worker.id, StaffAssignment.ended_at.is_(None)))


async def test_the_workers_list_has_everyone_with_what_needs_attention_first(client, team):
    await add_rate(60_000)
    await add_recording(CAM, 60)
    await link(team.cem, "cam-03@example.com")  # linked, but no staff member
    body = await users(client, team.admin)
    assert body["counts"] == {"workers": 8, "staff": 5}  # USR-1: every registered user, whatever their role
    rows = {r["name"]: r for r in body["rows"]}
    assert [r["name"] for r in body["rows"]][:2] == ["Bek Worker", "Cem Worker"]  # USR-4: attention first
    assert rows["Bek Worker"]["not_linked"] and rows["Bek Worker"]["no_staff"]
    assert not rows["Cem Worker"]["not_linked"] and rows["Cem Worker"]["no_staff"]
    ali = rows["Ali Worker"]  # USR-2
    assert (ali["linked"], ali["staff"]["name"], ali["remaining"], ali["role"]) == (True, "Sam Staff", 60_000, "worker")
    assert ali["phone_masked"].startswith("+998 ••") and team.ali.phone not in str(ali)
    assert rows["Sam Staff"]["role"] == "staff" and rows["Ada Admin"]["role"] == "admin" and rows["Boss Super"]["role"] == "super_admin"
    assert not rows["Sam Staff"]["not_linked"]  # staff who don't record are not flagged
    assert body["filters"] == {"not_linked": 1, "no_staff": 2, "owed": 1, "blocked": 0}


async def test_search_and_quick_filters(client, team):
    await add_rate(60_000)
    await add_recording(CAM, 60)
    names = lambda body: sorted(r["name"] for r in body["rows"])  # noqa: E731
    assert names(await users(client, team.admin, q="worker")) == ["Ali Worker", "Bek Worker", "Cem Worker"]  # USR-3: name
    assert names(await users(client, team.admin, q=team.bek.phone[-7:])) == ["Bek Worker"]  # phone
    assert names(await users(client, team.admin, q="CAM-01@")) == ["Ali Worker"]  # linked email
    assert names(await users(client, team.admin, filter="not_linked")) == ["Bek Worker", "Cem Worker"]
    assert names(await users(client, team.admin, filter="no_staff")) == ["Bek Worker", "Cem Worker"]
    assert names(await users(client, team.admin, filter="owed")) == ["Ali Worker"]
    await client.post(f"/api/users/{team.cem.id}/block", json={"blocked": True}, headers=auth(team.admin))
    assert names(await users(client, team.admin, filter="blocked")) == ["Cem Worker"]
    assert names(await users(client, team.admin, filter="owed", q="bek")) == []


async def test_the_staff_list(client, team):
    await add_rate(60_000)
    await add_recording(CAM, 60)
    await assign(team.bek, team.staff)
    await assign(team.cem, team.admin)
    body = await users(client, team.admin, tab="staff")
    rows = {r["name"]: r for r in body["rows"]}
    assert set(rows) == {"Sam Staff", "Sara Staff", "Ada Admin", "Adam Admin", "Boss Super"}  # USR-5
    assert rows["Ada Admin"]["role"] == "admin"
    sam = rows["Sam Staff"]  # USR-6: workers, their phones, total remaining owed to them
    assert (sam["workers"], sam["phones"], sam["remaining"]) == (2, 1, 60_000)
    assert (rows["Ada Admin"]["workers"], rows["Sara Staff"]["workers"]) == (1, 0)


async def test_the_profile(client, team):
    await add_rate(60_000)
    await add_recording(CAM, 60)
    await pay(team.staff, team.ali, 10_000)
    body = (await client.get(f"/api/users/{team.ali.id}", headers=auth(team.admin))).json()
    user = body["user"]  # USR-7
    assert (user["name"], user["phone"], user["role"], user["phone_verified"]) == ("Ali Worker", team.ali.phone, "worker", True)
    assert user["registered_at"] and body["email"] == CAM and body["staff"]["name"] == "Sam Staff"
    assert (body["summary"]["earned"], body["summary"]["paid"], body["summary"]["remaining"]) == (60_000, 10_000, 50_000)
    assert body["actions"] == {"make_staff": True, "remove_staff": False, "make_admin": False, "remove_admin": False,
                               "assign_staff": True, "block": True, "edit": False, "unlink": True}
    boss_view = (await client.get(f"/api/users/{team.ali.id}", headers=auth(team.boss))).json()["actions"]
    assert boss_view["make_admin"] and boss_view["edit"]
    assert (await client.get("/api/users/424242", headers=auth(team.admin))).status_code == 404


async def test_make_staff_and_remove_staff(client, team):
    r = await client.post(f"/api/users/{team.bek.id}/role", json={"role": "staff"}, headers=auth(team.admin))
    assert r.status_code == 200 and r.json()["role"] == "staff"
    me = (await client.get("/api/me", headers=auth(team.bek))).json()
    assert me["payments"] == ["money", "workers", "funds"]  # they gain My workers in Payments
    (told,) = await outbox_rows("user.role")  # USR-9: announced to the affected user
    assert told.chat_id == team.bek.telegram_id and "Staff" in told.text and "My workers" in told.text
    r = await client.post(f"/api/users/{team.bek.id}/role", json={"role": "worker"}, headers=auth(team.admin))
    assert r.status_code == 200
    assert (await client.get("/api/me", headers=auth(team.bek))).json()["payments"] == ["money"]
    async with db.ReadSession() as s:
        log = list(await s.scalars(select(AuditLog).where(AuditLog.action == "user.role_changed").order_by(AuditLog.id)))
    assert [(e.details["old"], e.details["new"], e.actor_id, e.target_user_id) for e in log] == [
        ("worker", "staff", team.admin.id, team.bek.id), ("staff", "worker", team.admin.id, team.bek.id)]


async def test_make_admin_and_remove_admin(client, team):
    r = await client.post(f"/api/users/{team.bek.id}/role", json={"role": "admin"}, headers=auth(team.boss))
    assert r.status_code == 200  # Worker becomes Admin
    assert "users" in (await client.get("/api/me", headers=auth(team.bek))).json()["sections"]
    r = await client.post(f"/api/users/{team.bek.id}/role", json={"role": "staff"}, headers=auth(team.boss))
    assert r.status_code == 200 and r.json()["role"] == "staff"  # Remove Admin: Admin becomes Staff
    r = await client.post(f"/api/users/{team.admin2.id}/role", json={"role": "worker"}, headers=auth(team.boss))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "role_change_invalid"  # not in one step
    r = await client.post(f"/api/users/{team.bek.id}/role", json={"role": "owner"}, headers=auth(team.boss))
    assert r.status_code == 400


async def test_removing_staff_is_refused_while_they_have_workers(client, team):
    await assign(team.bek, team.staff)
    code = await add_code("SAMSTAFF", staff=team.staff)
    r = await client.post(f"/api/users/{team.staff.id}/role", json={"role": "worker"}, headers=auth(team.admin))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "has_workers" and r.json()["detail"]["params"]["count"] == 2  # USR-14
    assert await current_staff(team.ali) == team.staff.id

    r = await client.post(f"/api/users/{team.staff.id}/role", json={"role": "worker", "move_workers_to": team.staff.id}, headers=auth(team.admin))
    assert r.status_code == 400
    r = await client.post(f"/api/users/{team.staff.id}/role", json={"role": "worker", "move_workers_to": team.cem.id}, headers=auth(team.admin))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_staff"

    r = await client.post(f"/api/users/{team.staff.id}/role", json={"role": "worker", "move_workers_to": team.staff2.id}, headers=auth(team.admin))
    assert r.status_code == 200 and r.json()["moved_workers"] == 2  # moved in one step
    assert await current_staff(team.ali) == team.staff2.id and await current_staff(team.bek) == team.staff2.id
    async with db.ReadSession() as s:
        assert (await s.get(InviteCode, code.id)).staff_id is None  # their code no longer assigns workers to them
        history = list(await s.scalars(select(StaffAssignment).where(StaffAssignment.worker_id == team.ali.id).order_by(StaffAssignment.id)))
    assert [(a.staff_id, a.ended_at is None) for a in history] == [(team.staff.id, False), (team.staff2.id, True)]
    told = await outbox_rows("staff.assigned")
    assert {m.chat_id for m in told} == {team.ali.telegram_id, team.bek.telegram_id, team.staff2.telegram_id}
    summary = next(m.text for m in told if m.chat_id == team.staff2.telegram_id)
    assert "2 workers were moved to you" in summary and "Ali Worker" in summary and "Bek Worker" in summary


async def test_assigning_and_changing_the_responsible_staff_member(client, team):
    r = await client.post(f"/api/users/{team.bek.id}/staff", json={"staff_id": team.staff.id}, headers=auth(team.admin))
    assert r.status_code == 200 and await current_staff(team.bek) == team.staff.id
    told = {m.chat_id: m.text for m in await outbox_rows("staff.assigned")}
    assert "Sam Staff" in told[team.bek.telegram_id] and "+998 90" in told[team.bek.telegram_id]  # each other's name and phone
    assert "Bek Worker" in told[team.staff.telegram_id]

    clock.shift(timedelta(minutes=5))
    r = await client.post(f"/api/users/{team.bek.id}/staff", json={"staff_id": team.staff2.id}, headers=auth(team.admin))
    assert r.json() == {"staff_id": team.staff2.id, "previous_staff_id": team.staff.id}
    async with db.ReadSession() as s:
        rows = list(await s.scalars(select(StaffAssignment).where(StaffAssignment.worker_id == team.bek.id).order_by(StaffAssignment.id)))
    # USR-10: at most one at a time; the earlier one is kept as history, with who set it
    assert [(a.staff_id, a.ended_at is None, a.set_by_id) for a in rows] == [(team.staff.id, False, team.admin.id), (team.staff2.id, True, team.admin.id)]
    assert any("no longer your worker" in m.text for m in await outbox_rows("staff.unassigned", team.staff.telegram_id))

    r = await client.post(f"/api/users/{team.bek.id}/staff", json={"staff_id": None}, headers=auth(team.admin))
    assert r.status_code == 200 and await current_staff(team.bek) is None
    row = next(r for r in (await users(client, team.admin))["rows"] if r["id"] == team.bek.id)
    assert row["no_staff"] is True  # listed as No staff assigned


async def test_who_can_be_a_responsible_staff_member(client, team):
    post = lambda worker, staff: client.post(f"/api/users/{worker.id}/staff", json={"staff_id": staff.id}, headers=auth(team.admin))  # noqa: E731
    r = await post(team.staff, team.staff)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "own_staff"  # USR-15
    r = await post(team.bek, team.cem)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_staff"
    assert (await post(team.bek, team.admin2)).status_code == 200  # an Admin can be responsible for workers
    assert (await post(team.staff, team.staff2)).status_code == 200  # a staff member who records has a staff member too
    options = (await client.get("/api/staff-options", headers=auth(team.admin))).json()["staff"]
    assert {o["name"] for o in options} == {"Sam Staff", "Sara Staff", "Ada Admin", "Adam Admin", "Boss Super"}
    await client.post(f"/api/users/{team.staff2.id}/block", json={"blocked": True}, headers=auth(team.admin))
    r = await post(team.cem, team.staff2)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "staff_blocked"


async def test_blocking_and_unblocking_are_announced(client, team):
    await client.post(f"/api/users/{team.bek.id}/block", json={"blocked": True}, headers=auth(team.admin))
    (blocked,) = await outbox_rows("user.blocked")
    assert blocked.chat_id == team.bek.telegram_id and "disabled" in blocked.text
    await client.post(f"/api/users/{team.bek.id}/block", json={"blocked": True}, headers=auth(team.admin))  # again: nothing new
    assert len(await outbox_rows("user.blocked")) == 1
    await client.post(f"/api/users/{team.bek.id}/block", json={"blocked": False}, headers=auth(team.admin))
    (restored,) = await outbox_rows("user.unblocked")
    assert "restored" in restored.text


async def test_the_super_admin_corrects_registration_data(client, team):
    r = await client.patch(f"/api/users/{team.bek.id}", json={"full_name": "Bek"}, headers=auth(team.boss))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "name_words"
    r = await client.patch(f"/api/users/{team.bek.id}", json={"phone": team.ali.phone}, headers=auth(team.boss))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "phone_taken"
    r = await client.patch(f"/api/users/{team.bek.id}", json={"full_name": "Bekzod Aliyev", "phone": "90 777 00 11"}, headers=auth(team.boss))
    assert r.json() == {"name": "Bekzod Aliyev", "phone": "+998907770011"}
    async with db.ReadSession() as s:
        bek = await s.get(User, team.bek.id)
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "user.edited"))
    assert bek.phone_verified is False and entry.actor_id == team.boss.id and set(entry.details["changes"]) == {"full_name", "phone"}


async def test_unlinking_an_email(client, team):
    r = await client.post(f"/api/users/{team.bek.id}/unlink", json={}, headers=auth(team.admin))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_linked"
    r = await client.post(f"/api/users/{team.ali.id}/unlink", json={}, headers=auth(team.admin))
    assert r.json() == {"email": CAM}
    assert (await client.get(f"/api/users/{team.ali.id}", headers=auth(team.admin))).json()["email"] is None
    async with db.ReadSession() as s:
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "link.removed"))
    assert entry.details == {"email": CAM, "keep_recordings": True} and entry.target_user_id == team.ali.id


async def test_the_audit_log_cannot_be_edited_or_deleted(client, team):
    """NFR-10: there is no endpoint for it, and the database itself refuses."""
    import pytest
    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    await client.post(f"/api/users/{team.bek.id}/block", json={"blocked": True}, headers=auth(team.admin))
    for statement in (update(AuditLog).values(action="nothing"), delete(AuditLog)):
        async with db.WriteSession() as s:
            with pytest.raises(DBAPIError, match="audit log"):
                await s.execute(statement)
    async with db.ReadSession() as s:
        assert [e.action for e in await s.scalars(select(AuditLog))] == ["user.blocked"]
