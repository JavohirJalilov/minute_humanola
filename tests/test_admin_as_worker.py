"""Admins and Super Admins can also record and be paid, exactly like workers."""

import pytest

from tests.conftest import TODAY, add_code, add_rate, add_recording, assign, auth, fund


@pytest.mark.parametrize("who", ["admin", "boss"])
async def test_an_admin_records_earns_and_is_paid_like_a_worker(client, team, mailbox, who):
    me = getattr(team, who)
    await add_code("87GSGM84")
    await add_rate(36_000)
    # links a UseMinute account
    assert (await client.post("/api/link/invite", json={"code": "87GSGM84"}, headers=auth(me))).json()["step"] == "email"
    assert (await client.post("/api/link/email", json={"email": f"{who}@example.com"}, headers=auth(me))).json()["step"] == "code"
    assert (await client.post("/api/link/verify", json={"code": mailbox.code}, headers=auth(me))).json()["step"] == "linked"
    await add_recording(f"{who}@example.com", 60, "accepted", day=TODAY)
    progress = (await client.get("/api/progress", headers=auth(me))).json()
    assert progress["linked"] and progress["total"]["accepted"]["amount"] == 36_000
    # gets a staff member, who sees and pays them
    await assign(me, team.staff)
    await fund(team.admin2, team.staff, 100_000)
    workers = (await client.get("/api/staff/workers", headers=auth(team.staff))).json()
    assert me.id in [w["id"] for w in workers["workers"]]
    r = await client.post("/api/payments", json={"worker_id": me.id, "amount": 36_000}, headers=auth(team.staff))
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/transfers/{r.json()['id']}/accept", headers=auth(me))).status_code == 200
    money = (await client.get("/api/payments/me", headers=auth(me))).json()
    assert money["summary"]["paid"] == 36_000 and money["summary"]["remaining"] == 0
    # shows up as a worker in Users and in the UseMinute account list
    people = (await client.get("/api/users", params={"q": me.full_name}, headers=auth(team.boss))).json()
    assert me.id in [p["id"] for p in people["rows"]]


async def test_an_admin_can_be_assigned_to_staff_by_the_super_admin(client, team):
    r = await client.post(f"/api/users/{team.admin.id}/staff", json={"staff_id": team.staff.id}, headers=auth(team.boss))
    assert r.status_code == 200, r.text
