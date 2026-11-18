"""Section 3: every row of the permission matrix, for each of the four roles, checked on the server
(ROLE-5), plus the rules ROLE-1..6."""

import pytest
from sqlalchemy import select

from backend import db
from backend.models import AWAITING, User
from tests.conftest import add_code, add_rate, add_recording, add_user, assign, auth, fund, link, pay

ROLES = ("worker", "staff", "admin", "super")
YES, NO = True, False


def actor(team, role: str) -> User:
    return {"worker": team.bek, "staff": team.staff, "admin": team.admin, "super": team.boss}[role]


def allowed(response, expected: bool) -> None:
    if expected:
        assert response.status_code < 300, f"should be allowed: {response.status_code} {response.text}"
    else:
        assert response.status_code == 403, f"should be refused: {response.status_code} {response.text}"


def row(worker, staff, admin, super_):
    return pytest.mark.parametrize("role, expected", list(zip(ROLES, (worker, staff, admin, super_))))


async def other_funder(team, me: User) -> User:
    """An Admin who is not `me`, to hand `me` their funding."""
    return team.admin2 if me.id != team.admin2.id else team.admin


# ---- the matrix


@row(YES, YES, YES, YES)
async def test_register_and_open_the_web_app(client, team, role, expected):
    allowed(await client.get("/api/me", headers=auth(actor(team, role))), expected)


@row(YES, YES, YES, YES)
async def test_link_own_doppi_account_and_see_own_phone(client, team, role, expected):
    me = actor(team, role)
    await add_code()
    allowed(await client.get("/api/link", headers=auth(me)), expected)
    allowed(await client.post("/api/link/invite", json={"code": "87GSGM84"}, headers=auth(me)), expected)
    allowed(await client.get("/api/progress", headers=auth(me)), expected)
    allowed(await client.get("/api/recordings", headers=auth(me)), expected)


@row(YES, YES, YES, YES)
async def test_see_own_earned_paid_remaining_and_payment_history(client, team, role, expected):
    me = actor(team, role)
    r = await client.get("/api/payments/me", headers=auth(me))
    allowed(r, expected)
    assert {"earned", "paid", "remaining", "pending_review", "rejected"} <= set(r.json()["summary"]) and "history" in r.json()


@row(YES, YES, YES, YES)
async def test_accept_or_decline_a_payment_addressed_to_them(client, team, role, expected):
    me = actor(team, role)
    payer = team.staff2
    first, second = await pay(payer, me, 10_000, AWAITING), await pay(payer, me, 20_000, AWAITING)
    allowed(await client.post(f"/api/transfers/{first.id}/accept", json={}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/transfers/{second.id}/decline", json={"reason": "no"}, headers=auth(me)), expected)


@row(NO, YES, YES, YES)
async def test_see_the_phones_and_money_of_workers_assigned_to_them(client, team, role, expected):
    me = actor(team, role)
    worker = await add_user(40, "Dina Worker")
    await link(worker, "cam-40@example.com")
    await assign(worker, me if role != "worker" else team.staff)
    allowed(await client.get("/api/staff/workers", headers=auth(me)), expected)
    allowed(await client.get(f"/api/staff/workers/{worker.id}", headers=auth(me)), expected)
    allowed(await client.get("/api/progress", params={"user_id": worker.id}, headers=auth(me)), expected)
    allowed(await client.get("/api/recordings", params={"user_id": worker.id}, headers=auth(me)), expected)


@row(NO, YES, YES, YES)
async def test_send_a_payment_to_a_worker_assigned_to_them(client, team, role, expected):
    me = actor(team, role)
    worker = await add_user(40, "Dina Worker")
    await assign(worker, me if role != "worker" else team.staff)
    if role != "worker":
        await fund(await other_funder(team, me), me, 100_000)
    r = await client.post("/api/payments", json={"worker_id": worker.id, "amount": 10_000, "confirm_advance": True}, headers=auth(me))
    allowed(r, expected)


@row(NO, YES, YES, YES)
async def test_confirm_or_decline_funding_received_from_an_admin(client, team, role, expected):
    me = actor(team, role)
    giver = await other_funder(team, me)
    r = await client.post("/api/fundings", json={"staff_id": me.id, "amount": 50_000}, headers=auth(giver))
    if not expected:
        assert r.status_code == 400 and r.json()["detail"]["code"] == "not_staff"  # a Worker is never given funding
        assert (await client.get("/api/staff/funds", headers=auth(me))).status_code == 403
        return
    allowed(await client.get("/api/staff/funds", headers=auth(me)), expected)
    allowed(await client.post(f"/api/transfers/{r.json()['id']}/accept", json={}, headers=auth(me)), expected)
    second = (await client.post("/api/fundings", json={"staff_id": me.id, "amount": 60_000}, headers=auth(giver))).json()["id"]
    allowed(await client.post(f"/api/transfers/{second}/decline", json={}, headers=auth(me)), expected)


@row(NO, NO, YES, YES)
async def test_see_every_staff_members_phones_totals_and_statements(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.get("/api/admin/staff", headers=auth(me)), expected)
    allowed(await client.get(f"/api/admin/staff/{team.staff2.id}", headers=auth(me)), expected)
    allowed(await client.get("/api/admin/given", headers=auth(me)), expected)
    allowed(await client.post("/api/admin/export/staff", json={}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/admin/export/statement/{team.staff2.id}", json={}, headers=auth(me)), expected)
    # a staff member can't look at a worker who is someone else's (NFR-2)
    allowed(await client.get(f"/api/staff/workers/{team.cem.id}", headers=auth(me)), expected)
    allowed(await client.get("/api/progress", params={"user_id": team.cem.id}, headers=auth(me)), expected)


@row(NO, NO, YES, YES)
async def test_give_funding_to_a_staff_member(client, team, role, expected):
    r = await client.post("/api/fundings", json={"staff_id": team.staff2.id, "amount": 50_000}, headers=auth(actor(team, role)))
    allowed(r, expected)


@row(NO, NO, YES, YES)
async def test_open_the_users_section(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.get("/api/users", headers=auth(me)), expected)  # APP-2: the server refuses its data too
    allowed(await client.get("/api/users", params={"tab": "staff"}, headers=auth(me)), expected)
    allowed(await client.get(f"/api/users/{team.cem.id}", headers=auth(me)), expected)
    allowed(await client.get("/api/staff-options", headers=auth(me)), expected)
    sections = (await client.get("/api/me", headers=auth(me))).json()["sections"]
    assert ("users" in sections) is expected


@row(NO, NO, YES, YES)
async def test_make_staff_and_remove_staff(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.post(f"/api/users/{team.cem.id}/role", json={"role": "staff"}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/users/{team.staff2.id}/role", json={"role": "worker"}, headers=auth(me)), expected)


@row(NO, NO, YES, YES)
async def test_assign_a_worker_to_a_staff_member(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.post(f"/api/users/{team.cem.id}/staff", json={"staff_id": team.staff2.id}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/users/{team.cem.id}/staff", json={"staff_id": None}, headers=auth(me)), expected)


@row(NO, NO, YES, YES)
async def test_block_or_unblock_a_user(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.post(f"/api/users/{team.cem.id}/block", json={"blocked": True}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/users/{team.cem.id}/block", json={"blocked": False}, headers=auth(me)), expected)


@row(NO, NO, YES, YES)
async def test_open_settings(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.get("/api/settings", headers=auth(me)), expected)
    r = await client.post("/api/settings/invite-codes", json={"label": "Team A"}, headers=auth(me))
    allowed(r, expected)
    code_id = r.json()["id"] if expected else (await add_code("ZZZZ9999")).id
    allowed(await client.patch(f"/api/settings/invite-codes/{code_id}", json={"is_active": False}, headers=auth(me)), expected)
    allowed(await client.delete(f"/api/settings/invite-codes/{code_id}", headers=auth(me)), expected)
    allowed(await client.post("/api/settings/rates", json={"amount": 20_000}, headers=auth(me)), expected)
    r = await client.post("/api/settings/sync", json={}, headers=auth(me))
    assert r.status_code == (409 if expected else 403)  # 409: UseMinute is not configured in the tests


@row(NO, NO, NO, YES)
async def test_make_admin_and_remove_admin(client, team, role, expected):
    me = actor(team, role)
    allowed(await client.post(f"/api/users/{team.cem.id}/role", json={"role": "admin"}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/users/{team.staff2.id}/role", json={"role": "admin"}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/users/{team.admin2.id}/role", json={"role": "staff"}, headers=auth(me)), expected)


@row(NO, NO, NO, YES)
async def test_edit_another_users_name_or_phone_number(client, team, role, expected):
    me = actor(team, role)
    r = await client.patch(f"/api/users/{team.cem.id}", json={"full_name": "Cem Corrected", "phone": "+998 90 555 00 11"}, headers=auth(me))
    allowed(r, expected)
    async with db.ReadSession() as s:
        cem = await s.get(User, team.cem.id)
    assert (cem.full_name, cem.phone) == (("Cem Corrected", "+998905550011") if expected else ("Cem Worker", team.cem.phone))


@row(NO, NO, NO, YES)
async def test_void_an_accepted_payment_or_funding(client, team, role, expected):
    me = actor(team, role)
    payment, funding = await pay(team.staff, team.ali, 10_000), await fund(team.admin2, team.staff, 50_000)
    allowed(await client.post(f"/api/transfers/{payment.id}/void", json={"reason": "mistake"}, headers=auth(me)), expected)
    allowed(await client.post(f"/api/transfers/{funding.id}/void", json={"reason": "mistake"}, headers=auth(me)), expected)


# ---- the rules


async def test_a_new_user_is_always_a_worker_with_exactly_one_role(chat, client):
    await chat.say(70, "/start", first_name="New", last_name="Person")
    await chat.say(70, "New Person")
    await chat.say(70, "901112233")
    me = (await client.get("/api/me", headers=auth(70))).json()
    assert me["user"]["role"] == "worker"  # ROLE-1
    assert me["sections"] == ["progress", "payments"] and me["payments"] == ["money"]


async def test_each_higher_role_keeps_what_the_role_below_can_do(client, team):
    sections = {}
    for role in ROLES:
        me = (await client.get("/api/me", headers=auth(actor(team, role)))).json()
        sections[role] = (me["user"]["role"], me["sections"], me["payments"], me["start"])
    assert sections == {
        "worker": ("worker", ["progress", "payments"], ["money"], "progress"),  # APP-4: first screen
        "staff": ("staff", ["progress", "payments"], ["money", "workers", "funds"], "progress"),
        "admin": ("admin", ["progress", "payments", "users", "useminute"], ["money", "workers", "funds", "staff"], "progress"),
        "super": ("super_admin", ["progress", "payments", "users", "useminute"], ["money", "workers", "funds", "staff"], "progress"),
    }


async def test_nobody_can_change_their_own_role_or_block_themselves(client, team):
    for me in (team.admin, team.boss):
        r = await client.post(f"/api/users/{me.id}/role", json={"role": "staff"}, headers=auth(me))
        assert r.status_code == 403 and r.json()["detail"]["code"] in ("own_role",)  # ROLE-2
        r = await client.post(f"/api/users/{me.id}/block", json={"blocked": True}, headers=auth(me))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "own_block"
        profile = (await client.get(f"/api/users/{me.id}", headers=auth(me))).json()
        assert not any(profile["actions"][a] for a in ("make_staff", "remove_staff", "make_admin", "remove_admin", "block"))


async def test_an_admin_cannot_change_block_or_demote_another_admin_or_a_super_admin(client, team):
    for target in (team.admin2, team.boss):
        for path, body in (("role", {"role": "staff"}), ("role", {"role": "worker"}), ("block", {"blocked": True}),
                           ("staff", {"staff_id": team.staff.id}), ("unlink", {})):
            r = await client.post(f"/api/users/{target.id}/{path}", json=body, headers=auth(team.admin))
            assert r.status_code == 403, (target.full_name, path, r.text)  # ROLE-3
        actions = (await client.get(f"/api/users/{target.id}", headers=auth(team.admin))).json()["actions"]
        assert not any(actions.values())
    async with db.ReadSession() as s:
        assert (await s.get(User, team.admin2.id)).role == "admin" and not (await s.get(User, team.admin2.id)).is_blocked


async def test_the_super_admin_role_comes_only_from_the_server_configuration(client, team, settings):
    assert team.boss.role == "worker"  # registered like anyone else (ROLE-4)
    assert (await client.get("/api/me", headers=auth(team.boss))).json()["user"]["role"] == "super_admin"
    for body in ({"role": "admin"}, {"role": "staff"}):
        r = await client.post(f"/api/users/{team.boss.id}/role", json=body, headers=auth(team.boss))
        assert r.status_code == 403
    other_super = await add_user(8888, "Second Boss")
    settings.super_admin_ids.append(8888)
    try:
        r = await client.post(f"/api/users/{other_super.id}/role", json={"role": "staff"}, headers=auth(team.boss))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "super_admin_fixed"
        r = await client.post(f"/api/users/{other_super.id}/block", json={"blocked": True}, headers=auth(team.boss))
        assert r.status_code == 403
    finally:
        settings.super_admin_ids.remove(8888)


async def test_a_role_change_takes_effect_on_the_next_request(client, team):
    assert (await client.get("/api/staff/workers", headers=auth(team.cem))).status_code == 403
    await client.post(f"/api/users/{team.cem.id}/role", json={"role": "staff"}, headers=auth(team.admin))
    assert (await client.get("/api/staff/workers", headers=auth(team.cem))).status_code == 200  # APP-5
    await client.post(f"/api/users/{team.cem.id}/role", json={"role": "worker"}, headers=auth(team.admin))
    assert (await client.get("/api/staff/workers", headers=auth(team.cem))).status_code == 403


async def test_a_blocked_user_cannot_open_the_web_app_and_keeps_their_data(client, team):
    await add_rate(60_000)
    await add_recording("cam-01@example.com", 60)
    await pay(team.staff, team.ali, 10_000)
    await client.post(f"/api/users/{team.ali.id}/block", json={"blocked": True}, headers=auth(team.admin))
    for path in ("/api/me", "/api/progress", "/api/payments/me", "/api/link"):
        r = await client.get(path, headers=auth(team.ali))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "blocked"  # ROLE-6
    profile = (await client.get(f"/api/users/{team.ali.id}", headers=auth(team.admin))).json()
    assert profile["user"]["blocked"] and profile["summary"]["earned"] == 60_000 and profile["summary"]["paid"] == 10_000
    await client.post(f"/api/users/{team.ali.id}/block", json={"blocked": False}, headers=auth(team.admin))
    assert (await client.get("/api/payments/me", headers=auth(team.ali))).json()["summary"]["remaining"] == 50_000


async def test_a_worker_sees_only_their_own_data(client, team):
    await link(team.bek, "cam-02@example.com")
    for params in ({"user_id": team.ali.id}, {"user_id": team.staff.id}):
        assert (await client.get("/api/progress", params=params, headers=auth(team.bek))).status_code == 403  # NFR-2
        assert (await client.get("/api/recordings", params=params, headers=auth(team.bek))).status_code == 403
    await add_recording("cam-01@example.com", 60)
    mine = (await client.get("/api/recordings", params={"email": "cam-01@example.com"}, headers=auth(team.bek))).json()
    assert mine["recordings"] == []  # naming someone else's phone gives nothing


async def test_requests_without_valid_telegram_data_are_refused(client, team):
    for headers in ({}, {"Authorization": "tma garbage"}, {"Authorization": "Bearer x"}, {"X-Debug-User-Id": "1"}):
        for path in ("/api/me", "/api/users", "/api/settings", "/api/admin/staff", "/api/staff/workers", "/api/payments/me"):
            assert (await client.get(path, headers=headers)).status_code == 401, (headers, path)
