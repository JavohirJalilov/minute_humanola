"""Section 8.1: every amount formula (CALC-1..7) and the rate rules (SET-7..10)."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from backend import db
from backend.models import ACCEPTED, AWAITING, CANCELLED, DECLINED, VOIDED, Recording
from backend.services import amounts
from tests.conftest import TODAY, add_rate, add_recording, add_user, assign, auth, fund, link, pay

CAM = "cam-01@example.com"


async def money(settings, user) -> amounts.Money:
    async with db.ReadSession() as s:
        return (await amounts.money_for(s, settings, [user.id]))[user.id]


async def test_the_example_of_section_8(team, settings):
    """Rate 20 000; 10 h accepted, 2 h pending, 1.5 h rejected; 120 000 accepted, 50 000 not answered."""
    await add_rate(20_000)
    await add_recording(CAM, 600, "accepted")
    await add_recording(CAM, 120, "pending")
    await add_recording(CAM, 90, "rejected")
    await pay(team.staff, team.ali, 120_000, ACCEPTED)
    await pay(team.staff, team.ali, 50_000, AWAITING)
    m = await money(settings, team.ali)
    assert m.earned == 200_000
    assert m.pending_review == 40_000
    assert m.rejected == 30_000
    assert m.paid == 120_000
    assert m.awaiting == 50_000
    assert m.remaining == 80_000  # CALC-3: the awaiting 50 000 does not reduce it
    assert m.overpaid == 0 and m.payable == 30_000


@pytest.mark.parametrize("minutes, rate, expected", [
    ([60], 20_000, 20_000),
    ([1], 20_000, 333),  # 333.33 → 333
    ([2], 20_000, 667),  # 666.67 → 667
    ([1], 30, 1),  # exactly half a so'm rounds up
    ([1, 1, 1], 20, 1),  # 3 × 0.33 is 1 so'm: summed first, rounded once (rounding each would give 0)
    ([20.5, 20.5, 20.5], 20_000, 20_500),  # 61.5 minutes, not rounded to hours or whole minutes
    ([0.25], 14_400, 60),  # 15 seconds
    ([12.3456], 50_000, 10_288),  # UseMinute's fractional minutes are kept
])
async def test_time_is_not_rounded_and_money_is_rounded_once(team, settings, minutes, rate, expected):
    await add_rate(rate)
    for m in minutes:
        await add_recording(CAM, m, "accepted")
    assert (await money(settings, team.ali)).earned == expected  # CALC-1


async def test_only_accepted_time_earns(team, settings):
    await add_rate(60_000)
    await add_recording(CAM, 10, "pending")
    await add_recording(CAM, 20, "rejected")
    m = await money(settings, team.ali)
    assert (m.earned, m.pending_review, m.rejected, m.remaining) == (0, 10_000, 20_000, 0)  # CALC-2
    assert m.split.minutes("pending") == 10 and m.split.minutes("rejected") == 20


async def test_only_accepted_payments_count_as_paid(team, settings):
    await add_rate(60_000)
    await add_recording(CAM, 60)
    for state in (AWAITING, DECLINED, CANCELLED, VOIDED):
        await pay(team.staff, team.ali, 10_000, state)
    m = await money(settings, team.ali)
    assert (m.paid, m.awaiting, m.remaining) == (0, 10_000, 60_000)
    await pay(team.staff, team.ali, 25_000, ACCEPTED)
    m = await money(settings, team.ali)
    assert (m.paid, m.remaining) == (25_000, 35_000)


async def test_paid_more_than_earned_is_overpaid_never_a_negative_debt(team, settings):
    await add_rate(60_000)
    await add_recording(CAM, 30)
    await pay(team.staff, team.ali, 50_000)
    m = await money(settings, team.ali)
    assert (m.earned, m.remaining, m.overpaid, m.payable) == (30_000, 0, 20_000, 0)  # CALC-4


async def test_earned_follows_a_re_review_and_paid_does_not(team, settings):
    await add_rate(60_000)
    rid = await add_recording(CAM, 60, "accepted")
    await pay(team.staff, team.ali, 40_000)
    assert (await money(settings, team.ali)).earned == 60_000
    async with db.WriteSession() as s:  # UseMinute re-reviews and rejects it
        await s.execute(update(Recording).where(Recording.id == rid).values(status="rejected", review_status="flagged"))
        await s.commit()
    m = await money(settings, team.ali)
    assert (m.earned, m.rejected, m.paid, m.overpaid) == (0, 60_000, 40_000, 40_000)  # CALC-5


async def test_each_recording_is_valued_at_the_rate_of_its_day(team, settings):
    await add_rate(20_000, date(2026, 10, 1))
    await add_recording(CAM, 60, day=date(2026, 10, 31))
    await add_recording(CAM, 60, day=date(2026, 11, 1))
    assert (await money(settings, team.ali)).earned == 40_000
    await add_rate(30_000, date(2026, 11, 1))  # a new rate from 1 November
    m = await money(settings, team.ali)
    assert m.earned == 20_000 + 30_000  # CALC-6, SET-8: October keeps its old value
    assert m.rate == 30_000


async def test_no_rate_means_money_is_not_available_but_time_is(team, settings, client):
    await add_recording(CAM, 90)
    m = await money(settings, team.ali)
    assert m.earned is None and m.pending_review is None and m.rejected is None  # SET-10
    assert m.available is False and m.split.minutes("accepted") == 90 and m.remaining == 0
    body = (await client.get("/api/progress", headers=auth(team.ali))).json()
    assert body["total"]["minutes"] == 90 and body["total"]["accepted"]["amount"] is None and body["rate"] is None


async def test_days_before_the_first_rate_earn_nothing_and_are_flagged(team, settings):
    await add_rate(60_000, date(2026, 11, 10))
    await add_recording(CAM, 60, day=date(2026, 11, 9))
    await add_recording(CAM, 60, day=date(2026, 11, 10))
    m = await money(settings, team.ali)
    assert m.earned == 60_000 and m.rate_missing is True and m.split.minutes("accepted") == 120


async def test_the_most_specific_rate_wins(team, settings):
    """SET-9: worker, then staff member, then company."""
    day = date(2026, 11, 5)
    await add_rate(20_000)
    await add_recording(CAM, 60, day=day)
    assert (await money(settings, team.ali)).earned == 20_000
    await add_rate(25_000, scope="staff", user=team.staff)
    assert (await money(settings, team.ali)).earned == 25_000
    await add_rate(30_000, scope="worker", user=team.ali)
    assert (await money(settings, team.ali)).earned == 30_000
    # a rate for another staff member or another worker changes nothing
    await add_rate(99_000, scope="staff", user=team.staff2)
    await add_rate(99_000, scope="worker", user=team.bek)
    assert (await money(settings, team.ali)).earned == 30_000


async def test_ending_an_override_brings_back_the_wider_rate(team, settings):
    await add_rate(20_000)
    await add_rate(30_000, date(2026, 11, 1), scope="worker", user=team.ali)
    await add_rate(None, date(2026, 11, 10), scope="worker", user=team.ali)  # the override ends
    await add_recording(CAM, 60, day=date(2026, 10, 20))  # company
    await add_recording(CAM, 60, day=date(2026, 11, 5))  # worker's own
    await add_recording(CAM, 60, day=date(2026, 11, 12))  # company again
    assert (await money(settings, team.ali)).earned == 20_000 + 30_000 + 20_000


async def test_a_staff_rate_applies_while_the_worker_was_with_that_staff_member(settings):
    """Moving a worker doesn't re-price earlier days: each day uses the staff member of that day."""
    staff_a, staff_b = await add_user(10, "Sam Staff", "staff"), await add_user(11, "Sara Staff", "staff")
    worker = await add_user(1, "Ali Worker")
    await link(worker, CAM)
    await add_rate(20_000)
    await add_rate(25_000, scope="staff", user=staff_a)
    await add_rate(40_000, scope="staff", user=staff_b)
    moved = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)  # 11:00 in Tashkent
    from backend.models import StaffAssignment

    async with db.WriteSession() as s:
        s.add(StaffAssignment(worker_id=worker.id, staff_id=staff_a.id, started_at=moved - timedelta(days=9), ended_at=moved, source="admin"))
        s.add(StaffAssignment(worker_id=worker.id, staff_id=staff_b.id, started_at=moved, source="admin"))
        await s.commit()
    await add_recording(CAM, 60, day=date(2026, 10, 25))  # before the first assignment: the first staff member's, 25 000
    await add_recording(CAM, 60, day=date(2026, 11, 5))  # with A: 25 000
    await add_recording(CAM, 60, day=date(2026, 11, 10))  # the day of the move belongs to B: 40 000
    await add_recording(CAM, 60, day=date(2026, 11, 14))  # with B: 40 000
    assert (await money(settings, worker)).earned == 25_000 + 25_000 + 40_000 + 40_000


async def test_balances_are_per_worker(team, settings):
    await add_rate(60_000)
    await link(team.bek, "cam-02@example.com")
    await add_recording(CAM, 60)
    await add_recording("cam-02@example.com", 30)
    await pay(team.staff, team.ali, 10_000)
    ali, bek = await money(settings, team.ali), await money(settings, team.bek)
    assert (ali.earned, ali.paid, ali.remaining) == (60_000, 10_000, 50_000)  # CALC-7
    assert (bek.earned, bek.paid, bek.remaining) == (30_000, 0, 30_000)
    assert (ali.phones, bek.phones) == (1, 1)


async def test_recordings_of_an_email_nobody_linked_belong_to_nobody(team, settings):
    await add_rate(60_000)
    await add_recording("stranger@example.com", 600)
    assert (await money(settings, team.ali)).earned == 0
    assert (await money(settings, team.cem)).earned == 0  # not linked at all


async def test_staff_cash_on_hand(team, settings):
    """Fundings the staff member confirmed, minus worker payments their workers accepted."""
    await fund(team.admin, team.staff, 500_000, ACCEPTED)
    await fund(team.admin, team.staff, 200_000, AWAITING)
    await fund(team.admin, team.staff, 900_000, DECLINED)
    await pay(team.staff, team.ali, 120_000, ACCEPTED)
    await pay(team.staff, team.ali, 50_000, AWAITING)
    await pay(team.staff, team.ali, 70_000, CANCELLED)
    async with db.ReadSession() as s:
        cash = (await amounts.cash_for(s, [team.staff.id, team.staff2.id]))
    c = cash[team.staff.id]
    assert (c.received, c.paid_out, c.on_hand) == (500_000, 120_000, 380_000)
    assert (c.funding_awaiting, c.payments_awaiting, c.available) == (200_000, 50_000, 330_000)
    assert cash[team.staff2.id].on_hand == 0


@pytest.mark.parametrize("to_be_paid, on_hand, awaiting, expected", [
    (2_480_000, 1_900_000, 0, 580_000),
    (4_160_000, 1_620_000, 1_000_000, 1_540_000),
    (100_000, 150_000, 0, 0),  # never below zero
    (100_000, 60_000, 60_000, 0),
])
def test_needs_funding(to_be_paid, on_hand, awaiting, expected):
    cash = amounts.Cash(received=on_hand, funding_awaiting=awaiting)
    assert amounts.needs_funding(to_be_paid, cash) == expected  # PAYA-2


async def test_period_filter_counts_recording_days(team, settings, client):
    await add_rate(60_000)
    await add_recording(CAM, 10, day=date(2026, 10, 31))
    await add_recording(CAM, 20, day=date(2026, 11, 1))
    await add_recording(CAM, 30, "pending", day=date(2026, 11, 16))
    get = lambda **q: client.get("/api/progress", params=q, headers=auth(team.ali))  # noqa: E731
    assert (await get()).json()["total"]["minutes"] == 60  # PRG-5: all time by default
    november = (await get(start="2026-11-01", end="2026-11-30")).json()
    assert november["total"]["minutes"] == 50 and november["total"]["accepted"]["amount"] == 20_000
    assert november["total"]["pending"]["amount"] == 30_000
    assert (await get(start="2026-10-01", end="2026-10-31")).json()["total"]["minutes"] == 10
    assert (await get(start="2026-12-01", end="2026-12-31")).json()["total"]["minutes"] == 0


async def test_the_split_shows_time_share_and_money_of_each_part(team, client):
    await add_rate(20_000)
    await add_recording(CAM, 600, "accepted")
    await add_recording(CAM, 120, "pending")
    await add_recording(CAM, 90, "rejected", rejection_reason="Hands not visible: The hands leave the frame")
    body = (await client.get("/api/progress", headers=auth(team.ali))).json()
    total = body["total"]  # PRG-1..4
    assert total["minutes"] == 810
    assert (total["accepted"]["minutes"], total["accepted"]["percent"], total["accepted"]["amount"]) == (600, 74.1, 200_000)
    assert (total["pending"]["minutes"], total["pending"]["percent"], total["pending"]["amount"]) == (120, 14.8, 40_000)
    assert (total["rejected"]["minutes"], total["rejected"]["percent"], total["rejected"]["amount"]) == (90, 11.1, 30_000)
    (phone,) = body["phones"]
    assert phone["email"] == CAM and phone["current"] is True and phone["accepted"]["amount"] == 200_000

    r = await client.get("/api/recordings", params={"email": CAM}, headers=auth(team.ali))  # PRG-6
    rows = r.json()["recordings"]
    assert [x["status"] for x in rows] == ["rejected", "pending", "accepted"]  # newest first
    assert rows[0]["reason"].startswith("Hands not visible") and rows[0]["minutes"] == 90 and rows[0]["amount"] == 30_000
    only = (await client.get("/api/recordings", params={"status": "rejected"}, headers=auth(team.ali))).json()["recordings"]
    assert len(only) == 1  # PAYW-4: the rejected recordings with UseMinute's reason


async def test_time_waiting_for_review_is_counted_in_time_not_money(client, team, settings):
    """Pending review isn't money until accepted: staff totals carry it in minutes, the Excel table in hours."""
    import io

    from openpyxl import load_workbook

    from backend.services import export

    await add_rate(36_000)
    await add_recording("cam-01@example.com", 90, "pending")
    await add_recording("cam-01@example.com", 30, "rejected")
    mine = (await client.get("/api/staff/workers", headers=auth(team.staff))).json()
    assert mine["totals"]["pending_minutes"] == 90
    assert mine["workers"][0]["minutes"] == {"accepted": 0, "pending": 90, "rejected": 30}
    company = (await client.get("/api/admin/staff", headers=auth(team.admin))).json()
    assert company["totals"]["pending_minutes"] == 90
    assert {s["name"]: s["pending_minutes"] for s in company["staff"]}["Sam Staff"] == 90
    async with db.ReadSession() as s:
        sheet = load_workbook(io.BytesIO(await export.staff_table(s, settings, "en"))).active
    header = [c.value for c in sheet[1]]
    column = header.index("Pending review, hours") + 1
    assert [row[column - 1].value for row in sheet.iter_rows(min_row=2) if row[0].value == "Sam Staff"] == [1.5]
