"""Section 9: the payment lifecycle (PAY-1..12) and the rules for sending (PAYS-5..10, PAYA-5..8).
Every state change is tested for who may make it and what it does to balances."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend import clock, db, jobs
from backend.models import ACCEPTED, AWAITING, CANCELLED, DECLINED, FUNDING, PAYMENT, VOIDED, AuditLog, Transfer
from backend.services import amounts
from tests.conftest import TODAY, add_rate, add_recording, assign, auth, bot_key, buttons_of, fund, link, outbox_rows, pay

CAM = "cam-01@example.com"


@pytest.fixture
async def funded(team):
    """Ali has earned 200 000; his staff member holds 500 000 in confirmed funding."""
    await add_rate(20_000)
    await add_recording(CAM, 600)
    await fund(team.admin, team.staff, 500_000)
    return team


async def send(client, staff, worker, amount, **body):
    return await client.post("/api/payments", json={"worker_id": worker.id, "amount": amount, **body}, headers=auth(staff))


async def give(client, admin, staff, amount, **body):
    return await client.post("/api/fundings", json={"staff_id": staff.id, "amount": amount, **body}, headers=auth(admin))


async def act(client, user, transfer_id, action, **body):
    return await client.post(f"/api/transfers/{transfer_id}/{action}", json=body, headers=auth(user))


async def state_of(transfer_id) -> Transfer:
    async with db.ReadSession() as s:
        return await s.get(Transfer, transfer_id)


async def figures(settings, team) -> tuple:
    async with db.ReadSession() as s:
        m = (await amounts.money_for(s, settings, [team.ali.id]))[team.ali.id]
        c = (await amounts.cash_for(s, [team.staff.id]))[team.staff.id]
    return m.paid, m.remaining, m.awaiting, c.on_hand


# ---- creating


async def test_a_payment_starts_as_awaiting_and_changes_no_balance(client, funded, settings):
    r = await send(client, funded.staff, funded.ali, 50_000, note="  Cash,   week 46 ")
    assert r.status_code == 200 and r.json()["state"] == AWAITING  # PAY-1
    t = await state_of(r.json()["id"])
    # PAY-12: kind, sender, receiver, amount, date paid, note, state, time created
    assert (t.kind, t.sender_id, t.receiver_id, t.amount) == (PAYMENT, funded.staff.id, funded.ali.id, 50_000)
    assert (t.paid_on, t.note, t.state, t.answered_at) == (settings.today(), "Cash, week 46", AWAITING, None)  # PAYS-5: today by default
    assert t.created_at is not None
    assert await figures(settings, funded) == (0, 200_000, 50_000, 500_000)


async def test_the_receiver_is_asked_in_the_bot_and_in_the_web_app(client, funded):
    r = await send(client, funded.staff, funded.ali, 50_000, note="Cash")
    (ask,) = await outbox_rows("transfer.ask")
    assert ask.chat_id == funded.ali.telegram_id and "Did you receive this money?" in ask.text  # PAY-2
    assert "50 000" in ask.text and "Sam Staff" in ask.text and "Cash" in ask.text
    assert [b["text"] for b in ask.buttons[0]] == ["✅ Accept", "⛔ Decline"]
    mine = (await client.get("/api/payments/me", headers=auth(funded.ali))).json()
    assert [t["id"] for t in mine["awaiting"]] == [r.json()["id"]] and mine["awaiting"][0]["can_answer"] is True


async def test_funding_is_asked_with_received_and_not_received(client, team):
    r = await give(client, team.admin, team.staff, 1_000_000, note="November")
    assert r.status_code == 200 and (await state_of(r.json()["id"])).kind == FUNDING
    (ask,) = await outbox_rows("transfer.ask")
    assert ask.chat_id == team.staff.telegram_id
    assert [b["text"] for b in ask.buttons[0]] == ["✅ Received", "⛔ Not received"]


@pytest.mark.parametrize("amount", [0, -5, 1_000_000_001, 2**40])
async def test_amount_must_be_between_1_and_a_billion(client, funded, amount):
    r = await send(client, funded.staff, funded.ali, amount, confirm_advance=True)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "amount_invalid"  # PAYS-7
    r = await give(client, funded.admin, funded.staff, amount)
    assert r.status_code == 400


async def test_amount_must_be_a_whole_number(client, funded):
    r = await send(client, funded.staff, funded.ali, 100.5)
    assert r.status_code == 422
    assert "100.5" not in r.text  # what was typed is never echoed back
    r = await send(client, funded.staff, funded.ali, 1)  # the smallest amount
    assert r.status_code == 200


async def test_date_paid_is_never_in_the_future(client, funded, settings):
    tomorrow = (settings.today() + timedelta(days=1)).isoformat()
    r = await send(client, funded.staff, funded.ali, 10_000, paid_on=tomorrow)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "date_in_future"  # PAYS-5
    r = await give(client, funded.admin, funded.staff, 10_000, paid_on=tomorrow)
    assert r.status_code == 400
    earlier = (settings.today() - timedelta(days=3)).isoformat()
    r = await send(client, funded.staff, funded.ali, 10_000, paid_on=earlier)
    assert r.status_code == 200 and (await state_of(r.json()["id"])).paid_on.isoformat() == earlier


async def test_more_than_remaining_needs_a_second_confirmation(client, funded):
    r = await send(client, funded.staff, funded.ali, 200_001)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "advance_confirmation"  # PAYS-8
    assert r.json()["detail"]["params"] == {"remaining": 200_000, "awaiting": 0, "payable": 200_000}
    assert await outbox_rows("transfer.ask") == []
    r = await send(client, funded.staff, funded.ali, 200_001, confirm_advance=True)
    assert r.status_code == 200  # allowed, as an advance
    # what already awaits confirmation counts against the next payment (PAYS-6)
    r = await send(client, funded.staff, funded.ali, 1)
    assert r.status_code == 409 and r.json()["detail"]["params"]["payable"] == 0


async def test_staff_cannot_pay_out_more_than_their_confirmed_cash(client, funded, team):
    r = await send(client, team.staff, team.ali, 500_001, confirm_advance=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "insufficient_cash"  # PAYS-9
    assert r.json()["detail"]["params"]["available"] == 500_000
    assert (await send(client, team.staff, team.ali, 150_000)).status_code == 200
    # money already sent and waiting for an answer is no longer on hand to promise again
    r = await send(client, team.staff, team.ali, 400_000, confirm_advance=True)
    assert r.status_code == 409 and r.json()["detail"]["params"]["available"] == 350_000
    # funding that is not confirmed yet can't be paid out
    await fund(team.admin, team.staff, 1_000_000, AWAITING)
    assert (await send(client, team.staff, team.ali, 400_000, confirm_advance=True)).status_code == 409


async def test_a_double_tap_creates_one_payment(client, funded):
    body = {"client_key": "form-1"}
    first, second = await asyncio.gather(send(client, funded.staff, funded.ali, 50_000, **body),
                                         send(client, funded.staff, funded.ali, 50_000, **body))
    assert first.status_code == second.status_code == 200  # PAYS-10, NFR-8
    assert first.json()["id"] == second.json()["id"]
    assert sorted([first.json()["created"], second.json()["created"]]) == [False, True]
    async with db.ReadSession() as s:
        assert len(list(await s.scalars(select(Transfer).where(Transfer.kind == PAYMENT)))) == 1
    assert len(await outbox_rows("transfer.ask")) == 1
    third = await send(client, funded.staff, funded.ali, 50_000, client_key="form-2")  # a new form is a new payment
    assert third.json()["id"] != first.json()["id"]


async def test_only_their_own_workers_can_be_paid(client, funded, team):
    r = await send(client, team.staff, team.bek, 10_000, confirm_advance=True)  # bek has no staff member
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_worker"  # USR-10
    await assign(team.bek, team.staff2)
    assert (await send(client, team.staff, team.bek, 10_000, confirm_advance=True)).status_code == 403
    assert (await send(client, team.staff, team.staff, 10_000)).status_code == 403
    assert (await send(client, team.staff, type("X", (), {"id": 9999})(), 10_000)).status_code == 404


async def test_funding_goes_to_staff_and_never_to_oneself(client, team):
    r = await give(client, team.admin, team.admin, 100_000)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "self_funding"  # PAYA-8
    r = await give(client, team.admin, team.ali, 100_000)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_staff"
    assert (await give(client, team.admin, team.admin2, 100_000)).status_code == 200  # an Admin funds another Admin
    assert (await give(client, team.admin, team.boss, 100_000)).status_code == 200
    assert (await give(client, team.staff, team.staff2, 100_000)).status_code == 403


# ---- answering


async def test_accepting_a_payment_raises_paid_and_lowers_staff_cash(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 120_000, note="Cash")).json()["id"]
    r = await act(client, funded.ali, tid, "accept")
    assert r.status_code == 200 and r.json()["state"] == ACCEPTED
    t = await state_of(tid)
    assert t.state == ACCEPTED and t.answered_at is not None and t.reason is None
    assert await figures(settings, funded) == (120_000, 80_000, 0, 380_000)
    (told,) = await outbox_rows("transfer.answer")  # PAY-6: the sender is told the answer
    assert told.chat_id == funded.staff.telegram_id and "Ali Worker accepted your payment" in told.text


async def test_declining_changes_no_balance_and_tells_the_sender_and_the_admins(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 120_000)).json()["id"]
    r = await act(client, funded.ali, tid, "decline", reason="  I was given   only 100 000 ")
    assert r.status_code == 200 and r.json()["state"] == DECLINED
    t = await state_of(tid)
    assert t.state == DECLINED and t.reason == "I was given only 100 000" and t.answered_at is not None  # PAY-3
    assert await figures(settings, funded) == (0, 200_000, 0, 500_000)
    (told,) = await outbox_rows("transfer.answer")
    assert told.chat_id == funded.staff.telegram_id and "only 100 000" in told.text
    admins = {m.chat_id: m.text for m in await outbox_rows("transfer.declined")}  # PAY-9
    assert set(admins) == {funded.admin.telegram_id, funded.admin2.telegram_id, funded.boss.telegram_id}
    assert all("Ali Worker" in text and "Sam Staff" in text and "only 100 000" in text for text in admins.values())


async def test_the_decline_reason_is_optional(client, funded):
    tid = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    assert (await act(client, funded.ali, tid, "decline")).status_code == 200
    assert (await state_of(tid)).reason is None


async def test_confirming_funding_raises_staff_cash(client, team, settings):
    tid = (await give(client, team.admin, team.staff, 700_000)).json()["id"]
    async with db.ReadSession() as s:
        assert (await amounts.cash_for(s, [team.staff.id]))[team.staff.id].on_hand == 0
    assert (await act(client, team.staff, tid, "accept")).status_code == 200
    async with db.ReadSession() as s:
        assert (await amounts.cash_for(s, [team.staff.id]))[team.staff.id].on_hand == 700_000
    (told,) = await outbox_rows("transfer.answer")
    assert told.chat_id == team.admin.telegram_id and "Sam Staff confirmed receiving" in told.text


async def test_declined_funding_reaches_the_other_admins_but_not_twice_the_sender(client, team):
    tid = (await give(client, team.admin, team.staff, 700_000)).json()["id"]
    await act(client, team.staff, tid, "decline", reason="Not yet")
    assert [m.chat_id for m in await outbox_rows("transfer.answer")] == [team.admin.telegram_id]
    assert {m.chat_id for m in await outbox_rows("transfer.declined")} == {team.admin2.telegram_id, team.boss.telegram_id}


async def test_only_the_receiver_can_answer(client, funded, team):
    tid = (await send(client, team.staff, team.ali, 10_000)).json()["id"]
    for other in (team.staff, team.bek, team.admin, team.boss):
        for action in ("accept", "decline"):
            r = await act(client, other, tid, action)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "not_receiver"  # PAY-3
    assert (await state_of(tid)).state == AWAITING
    assert (await act(client, team.ali, 424242, "accept")).status_code == 404


@pytest.mark.parametrize("first", ["accept", "decline"])
async def test_an_answer_is_final_and_given_once(client, funded, first):
    tid = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    assert (await act(client, funded.ali, tid, first)).status_code == 200
    for again in ("accept", "decline"):
        r = await act(client, funded.ali, tid, again)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "already_answered"  # PAY-5
    assert (await state_of(tid)).state == (ACCEPTED if first == "accept" else DECLINED)
    assert len(await outbox_rows("transfer.answer")) == 1


async def test_bot_and_web_app_tapped_at_the_same_moment_save_one_answer(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    from_bot = client.post(f"/internal/transfers/{tid}/answer", json={"telegram_id": funded.ali.telegram_id, "accept": False},
                           headers=bot_key())
    from_app = act(client, funded.ali, tid, "accept")
    a, b = await asyncio.gather(from_bot, from_app)
    assert sorted([a.status_code, b.status_code]) == [200, 409]  # PAY-5, NFR-8: one tap, one result
    loser = a if a.status_code == 409 else b
    assert loser.json()["detail"]["code"] == "already_answered"
    winner_state = DECLINED if a.status_code == 200 else ACCEPTED
    assert (await state_of(tid)).state == winner_state
    assert len(await outbox_rows("transfer.answer")) == 1
    async with db.ReadSession() as s:
        entries = list(await s.scalars(select(AuditLog).where(AuditLog.entity == "transfer", AuditLog.entity_id == str(tid))))
    assert sorted(e.action for e in entries) == sorted(["transfer.created", f"transfer.{winner_state}"])


# ---- cancelling


async def test_the_sender_can_cancel_while_it_is_awaiting(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 50_000)).json()["id"]
    for other in (funded.ali, funded.admin, funded.boss, funded.staff2):
        r = await act(client, other, tid, "cancel")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_sender"  # PAY-4
    r = await act(client, funded.staff, tid, "cancel")
    assert r.status_code == 200 and r.json()["state"] == CANCELLED
    t = await state_of(tid)
    assert t.state == CANCELLED and t.cancelled_at is not None
    assert await figures(settings, funded) == (0, 200_000, 0, 500_000)
    (told,) = await outbox_rows("transfer.cancelled")
    assert told.chat_id == funded.ali.telegram_id and "cancelled" in told.text
    # nothing can be done with it afterwards
    for user, action in ((funded.ali, "accept"), (funded.ali, "decline"), (funded.staff, "cancel")):
        assert (await act(client, user, tid, action)).status_code == 409
    assert (await act(client, funded.boss, tid, "void", reason="x")).status_code == 409


@pytest.mark.parametrize("answer", ["accept", "decline"])
async def test_an_answered_transfer_cannot_be_cancelled(client, funded, answer):
    tid = (await send(client, funded.staff, funded.ali, 50_000)).json()["id"]
    await act(client, funded.ali, tid, answer)
    r = await act(client, funded.staff, tid, "cancel")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_answered"


async def test_a_wrong_amount_is_fixed_by_cancelling_and_sending_again(client, funded, settings):
    wrong = (await send(client, funded.staff, funded.ali, 150_000)).json()["id"]  # PAY-8: never edited
    await act(client, funded.staff, wrong, "cancel")
    right = (await send(client, funded.staff, funded.ali, 15_000)).json()["id"]
    await act(client, funded.ali, right, "accept")
    assert await figures(settings, funded) == (15_000, 185_000, 0, 485_000)
    assert (await state_of(wrong)).amount == 150_000  # still in the history, unchanged


# ---- voiding


async def test_a_super_admin_voids_an_accepted_transfer_with_a_reason(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 120_000)).json()["id"]
    await act(client, funded.ali, tid, "accept")
    for other in (funded.admin, funded.staff, funded.ali):
        assert (await act(client, other, tid, "void", reason="mistake")).status_code == 403
    r = await act(client, funded.boss, tid, "void", reason="   ")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "reason_required"
    r = await act(client, funded.boss, tid, "void", reason="Entered twice")
    assert r.status_code == 200 and r.json()["state"] == VOIDED
    t = await state_of(tid)
    assert (t.state, t.reason, t.voided_by_id) == (VOIDED, "Entered twice", funded.boss.id) and t.voided_at is not None
    assert await figures(settings, funded) == (0, 200_000, 0, 500_000)  # its effect is removed
    told = {m.chat_id: m.text for m in await outbox_rows("transfer.voided")}  # PAY-8: both parties are told
    assert set(told) == {funded.ali.telegram_id, funded.staff.telegram_id}
    assert all("Entered twice" in text for text in told.values())
    assert (await act(client, funded.boss, tid, "void", reason="again")).status_code == 409  # nobody can change it now


@pytest.mark.parametrize("state", [AWAITING, DECLINED, CANCELLED])
async def test_only_an_accepted_transfer_can_be_voided(client, funded, state):
    t = await pay(funded.staff, funded.ali, 10_000, state)
    r = await act(client, funded.boss, t.id, "void", reason="mistake")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_accepted"


async def test_voiding_funding_lowers_staff_cash_again(client, team):
    tid = (await give(client, team.admin, team.staff, 700_000)).json()["id"]
    await act(client, team.staff, tid, "accept")
    assert (await act(client, team.boss, tid, "void", reason="Wrong person")).status_code == 200
    async with db.ReadSession() as s:
        assert (await amounts.cash_for(s, [team.staff.id]))[team.staff.id].on_hand == 0


# ---- the rest of the rules


async def test_a_blocked_user_cannot_answer_but_the_sender_can_cancel(client, funded, team):
    tid = (await send(client, team.staff, team.ali, 10_000)).json()["id"]
    assert (await client.post(f"/api/users/{team.ali.id}/block", json={"blocked": True}, headers=auth(team.admin))).status_code == 200
    assert (await act(client, team.ali, tid, "accept")).status_code == 403  # PAY-11
    r = await client.post(f"/internal/transfers/{tid}/answer", json={"telegram_id": team.ali.telegram_id, "accept": True}, headers=bot_key())
    assert r.status_code == 403 and r.json()["detail"]["code"] == "blocked"
    assert (await state_of(tid)).state == AWAITING
    assert (await act(client, team.staff, tid, "cancel")).status_code == 200
    r = await send(client, team.staff, team.ali, 10_000)  # and no new payment to someone who can't confirm
    assert r.status_code == 400 and r.json()["detail"]["code"] == "receiver_blocked"


async def test_a_payment_stays_with_the_staff_member_who_sent_it(client, funded, team, settings):
    tid = (await send(client, team.staff, team.ali, 120_000)).json()["id"]
    await act(client, team.ali, tid, "accept")
    r = await client.post(f"/api/users/{team.ali.id}/staff", json={"staff_id": team.staff2.id}, headers=auth(team.admin))
    assert r.status_code == 200
    assert (await state_of(tid)).sender_id == team.staff.id  # PAY-10, USR-13
    async with db.ReadSession() as s:
        cash = await amounts.cash_for(s, [team.staff.id, team.staff2.id])
    assert cash[team.staff.id].on_hand == 380_000 and cash[team.staff2.id].on_hand == 0
    # the worker's remaining amount moves with the worker to the new staff member
    mine = (await client.get("/api/staff/workers", headers=auth(team.staff2))).json()
    assert [(w["id"], w["remaining"], w["paid"]) for w in mine["workers"]] == [(team.ali.id, 80_000, 120_000)]
    assert (await client.get("/api/staff/workers", headers=auth(team.staff))).json()["workers"] == []


async def test_nothing_is_accepted_automatically_reminders_go_out_instead(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    assert await jobs.send_reminders(settings) == 0
    clock.shift(timedelta(hours=23, minutes=59))
    assert await jobs.send_reminders(settings) == 0
    clock.shift(timedelta(minutes=2))
    assert await jobs.send_reminders(settings) == 1  # PAY-7: after 24 hours
    (reminder,) = await outbox_rows("transfer.reminder")
    assert reminder.chat_id == funded.ali.telegram_id and "Reminder" in reminder.text
    assert [b["data"] for b in reminder.buttons[0]] == [f"tr:{tid}:a", f"tr:{tid}:d"]  # the same buttons
    assert await jobs.send_reminders(settings) == 0  # then once a day
    for day in range(2, 8):
        clock.shift(timedelta(hours=24))
        assert await jobs.send_reminders(settings) == 1, day
    clock.shift(timedelta(hours=24))
    assert await jobs.send_reminders(settings) == 0  # for up to 7 days
    assert len(await outbox_rows("transfer.reminder")) == 7
    assert (await state_of(tid)).state == AWAITING


async def test_an_answered_transfer_gets_no_reminder(client, funded, settings):
    tid = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    await act(client, funded.ali, tid, "accept")
    clock.shift(timedelta(days=2))
    assert await jobs.send_reminders(settings) == 0


async def test_every_state_change_is_in_the_audit_log(client, funded):
    a = (await send(client, funded.staff, funded.ali, 10_000)).json()["id"]
    await act(client, funded.ali, a, "accept")
    await act(client, funded.boss, a, "void", reason="mistake")
    b = (await send(client, funded.staff, funded.ali, 20_000)).json()["id"]
    await act(client, funded.ali, b, "decline", reason="no")
    c = (await send(client, funded.staff, funded.ali, 30_000)).json()["id"]
    await act(client, funded.staff, c, "cancel")
    async with db.ReadSession() as s:
        rows = list(await s.scalars(select(AuditLog).where(AuditLog.entity == "transfer").order_by(AuditLog.id)))
    assert [(r.action, r.entity_id, r.actor_id) for r in rows] == [
        ("transfer.created", str(a), funded.staff.id), ("transfer.accepted", str(a), funded.ali.id),
        ("transfer.voided", str(a), funded.boss.id),
        ("transfer.created", str(b), funded.staff.id), ("transfer.declined", str(b), funded.ali.id),
        ("transfer.created", str(c), funded.staff.id), ("transfer.cancelled", str(c), funded.staff.id),
    ]
    assert all(r.at is not None for r in rows)


async def test_the_statement_shows_cash_after_each_line_that_counts(client, team):
    await add_rate(20_000)
    await add_recording(CAM, 600)
    f1 = (await give(client, team.admin, team.staff, 500_000)).json()["id"]
    await act(client, team.staff, f1, "accept")
    p1 = (await send(client, team.staff, team.ali, 120_000)).json()["id"]
    await act(client, team.ali, p1, "accept")
    p2 = (await send(client, team.staff, team.ali, 50_000)).json()["id"]  # awaiting: doesn't count
    f2 = (await give(client, team.admin, team.staff, 300_000)).json()["id"]
    await act(client, team.staff, f2, "decline")
    body = (await client.get("/api/staff/funds", headers=auth(team.staff))).json()
    lines = [(x["id"], x["incoming"], x["state"], x["balance_after"]) for x in body["statement"]]  # PAYS-13: newest first
    assert lines == [(f2, True, DECLINED, None), (p2, False, AWAITING, None), (p1, False, ACCEPTED, 380_000), (f1, True, ACCEPTED, 500_000)]
    assert body["position"] == {"received": 500_000, "paid_out": 120_000, "cash_on_hand": 380_000, "funding_awaiting": 0,
                                "payments_awaiting": 50_000, "available": 330_000}  # PAYS-11
