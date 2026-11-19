"""What each Web App screen shows (sections 6, 7.2 and 8), as plain data.

Every function takes the person the data is about; whether the caller may see it is decided in app/api.
All figures come from backend/services/amounts.py.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import roles
from backend.config import Settings
from backend.models import ACCEPTED, AWAITING, FUNDING, PAYMENT, REC_PENDING, REC_REJECTED, DoppiLink, Phone, Recording, StaffAssignment, Transfer, User
from backend.services import amounts, doppi, formats, people, transfers
from backend.services.amounts import STATUSES, Money, Split

LIST_LIMIT = 300


# ---- small pieces


def iso(moment) -> str | None:
    return moment.isoformat() if moment else None


def person(user: User | None) -> dict | None:
    return {"id": user.id, "name": user.full_name} if user else None


def contact(user: User | None) -> dict | None:
    """Name with the full phone number and Telegram username: for someone who needs to reach them."""
    if user is None:
        return None
    return {"id": user.id, "name": user.full_name, "phone": user.phone, "username": user.username}


def split_out(split: Split, available: bool) -> dict:
    """Recording time split into accepted, pending and rejected: time, share and money of each part (PRG-1..3)."""
    total = split.total_ms
    out = {"minutes": total / amounts.MS_PER_MINUTE, "recordings": sum(split.counts.values())}
    for status in STATUSES:
        out[status] = {
            "minutes": split.minutes(status),
            "percent": round(split.ms[status] / total * 100, 1) if total else 0.0,
            "amount": split.amount(status) if available else None,
            "recordings": split.counts[status],
        }
    return out


def money_out(money: Money) -> dict:
    return {
        "earned": money.earned,
        "paid": money.paid,
        "remaining": money.remaining,
        "overpaid": money.overpaid,
        "pending_review": money.pending_review,
        "rejected": money.rejected,
        "awaiting": money.awaiting,
        "payable": money.payable,
        "rate": money.rate,
        "rate_missing": money.rate_missing,
        "available": money.available,
        "minutes": {s: money.split.minutes(s) for s in STATUSES},
    }


async def users_by_id(session: AsyncSession, ids) -> dict[int, User]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}


def transfer_out(t: Transfer, users: dict[int, User], viewer: User, settings: Settings) -> dict:
    return {
        "id": t.id,
        "kind": t.kind,
        "amount": t.amount,
        "paid_on": t.paid_on.isoformat(),
        "note": t.note,
        "state": t.state,
        "created_at": iso(t.created_at),
        "answered_at": iso(t.answered_at),
        "reason": t.reason,
        "sender": person(users.get(t.sender_id)),
        "receiver": person(users.get(t.receiver_id)),
        # What this viewer may do with it (the server checks again when they do)
        "can_answer": t.state == AWAITING and t.receiver_id == viewer.id,
        "can_cancel": t.state == AWAITING and t.sender_id == viewer.id,
        "can_void": t.state == ACCEPTED and roles.is_super_admin(viewer, settings),
    }


async def transfers_out(session: AsyncSession, rows: list[Transfer], viewer: User, settings: Settings) -> list[dict]:
    users = await users_by_id(session, {t.sender_id for t in rows} | {t.receiver_id for t in rows})
    return [transfer_out(t, users, viewer, settings) for t in rows]


async def staff_of(session: AsyncSession, worker_id: int) -> User | None:
    staff_id = await transfers.current_staff_id(session, worker_id)
    return await session.get(User, staff_id) if staff_id else None


# ---- My Progress (section 7.2)


async def progress(session: AsyncSession, settings: Settings, user: User, start: date | None = None, end: date | None = None) -> dict:
    """The user's phone with its recording time and money, for recording days start..end (PRG-1..5)."""
    owned = (await amounts.owned_emails(session, [user.id])).get(user.id, [])
    book = await amounts.RateBook.load(session, settings.tz, [user.id])
    by_email = (await amounts.earnings(session, book, {user.id: owned}, start, end)).get(user.id, {})
    total = amounts.total_of(by_email)
    rate = book.rate_on(user.id, settings.today())
    available = rate is not None or total.unrated_ms < total.total_ms
    info = {}
    if owned:
        info = {p.email: p for p in await session.scalars(select(Phone).where(Phone.email.in_([o.email for o in owned])))}
    phones = []
    for o in owned:
        p = info.get(o.email)
        phones.append({
            "email": o.email,  # the phone's ID
            "current": o.current,  # False: an earlier email whose recordings still count (LNK-14)
            "model": p.model if p else None,
            "first_recorded_at": iso(p.first_recorded_at) if p else None,
            "last_recorded_at": iso(p.last_recorded_at) if p else None,
            **split_out(by_email[o.email], available),
        })
    return {
        "linked": any(o.current for o in owned),
        "synced_at": iso(await doppi.last_updated(session)),
        "rate": rate,
        "rate_missing": total.unrated_ms > 0,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "total": split_out(total, available),
        "phones": phones,
    }


async def recording_days(session: AsyncSession, settings: Settings, user: User, *, email: str | None = None,
                         start: date | None = None, end: date | None = None, limit: int = 30, offset: int = 0) -> dict:
    """A phone's recording days, newest first, each with its accepted, pending and rejected time and money.
    The recordings of a day come from `recordings` with start = end = that day."""
    owned = [o.email for o in (await amounts.owned_emails(session, [user.id])).get(user.id, [])]
    emails = [e for e in owned if email is None or e == email]
    synced_at = iso(await doppi.last_updated(session))
    if not emails:
        return {"days": [], "has_more": False, "synced_at": synced_at}
    where = [Recording.email.in_(emails)]
    if start is not None:
        where.append(Recording.day >= start)
    if end is not None:
        where.append(Recording.day <= end)
    days = list(await session.scalars(
        select(Recording.day).where(*where).group_by(Recording.day).order_by(Recording.day.desc()).limit(limit + 1).offset(offset)))
    shown = days[:limit]
    if not shown:
        return {"days": [], "has_more": False, "synced_at": synced_at}
    q = (select(Recording.day, Recording.status, func.sum(Recording.duration_ms), func.count())
         .where(*where, Recording.day.in_(shown)).group_by(Recording.day, Recording.status))
    splits: dict[date, amounts.Split] = {d: amounts.Split() for d in shown}
    book = await amounts.RateBook.load(session, settings.tz, [user.id])
    for day, status, ms, count in (await session.execute(q)).all():
        if status not in STATUSES:
            continue
        split, rate = splits[day], book.rate_on(user.id, day)
        split.ms[status] += int(ms or 0)
        split.counts[status] += count
        if rate is None:
            split.unrated_ms += int(ms or 0)
        else:
            split.weighted[status] += int(ms or 0) * rate
    out = [{"day": d.isoformat(), **split_out(splits[d], book.rate_on(user.id, d) is not None)} for d in shown]
    return {"days": out, "has_more": len(days) > limit, "synced_at": synced_at}


async def recordings(session: AsyncSession, settings: Settings, user: User, *, email: str | None = None,
                     start: date | None = None, end: date | None = None, status: str | None = None,
                     limit: int = 50, offset: int = 0) -> dict:
    """A phone's recordings, newest first: date, length, review status and UseMinute's reason for a rejection (PRG-6)."""
    owned = [o.email for o in (await amounts.owned_emails(session, [user.id])).get(user.id, [])]
    emails = [e for e in owned if email is None or e == email]
    if not emails:
        return {"recordings": [], "has_more": False, "synced_at": iso(await doppi.last_updated(session))}
    q = select(Recording).where(Recording.email.in_(emails))
    if start is not None:
        q = q.where(Recording.day >= start)
    if end is not None:
        q = q.where(Recording.day <= end)
    if status in STATUSES:
        q = q.where(Recording.status == status)
    rows = list(await session.scalars(q.order_by(Recording.recorded_at.desc(), Recording.id).limit(limit + 1).offset(offset)))
    book = await amounts.RateBook.load(session, settings.tz, [user.id])
    out = []
    for r in rows[:limit]:
        rate = book.rate_on(user.id, r.day)
        out.append({
            "id": r.id,
            "email": r.email,
            "recorded_at": iso(r.recorded_at),
            "day": r.day.isoformat(),
            "minutes": r.duration_ms / amounts.MS_PER_MINUTE,
            "status": r.status,
            "reason": r.rejection_reason or None,
            # UseMinute's verdict on a rejection: "feedback" (fixable) or "fraud"
            "verdict": r.verdict if r.status == REC_REJECTED else None,
            "flagged_videos": r.flagged_videos,
            "videos": r.chunks,
            "task": r.task_name,
            "category": r.category,
            "amount": amounts.amount_for(r.duration_ms, rate),
        })
    return {"recordings": out, "has_more": len(rows) > limit, "synced_at": iso(await doppi.last_updated(session))}


# ---- Payments: worker view (section 8.2)


async def my_money(session: AsyncSession, settings: Settings, user: User, viewer: User) -> dict:
    """The five figures, payments awaiting the worker's answer, the per-phone breakdown, the payment
    history and the responsible staff member (PAYW-1..7)."""
    money = (await amounts.money_for(session, settings, [user.id]))[user.id]
    rows = await transfers.received(session, user.id, PAYMENT, LIST_LIMIT)
    history = await transfers_out(session, rows, viewer, settings)
    phones = await progress(session, settings, user)
    return {
        "linked": phones["linked"],
        "synced_at": phones["synced_at"],
        "summary": money_out(money),
        "awaiting": [t for t in history if t["state"] == AWAITING],
        "history": history,
        "phones": phones["phones"],
        "staff": contact(await staff_of(session, user.id)),
    }


# ---- Payments: staff view (section 8.3)


def _worker_row(user: User, money: Money) -> dict:
    return {
        "id": user.id,
        "name": user.full_name,
        "phone_masked": formats.mask_phone(user.phone),
        "phones": money.phones,
        "blocked": user.is_blocked,
        **money_out(money),
    }


def _matches(user: User, q: str, email: str | None = None) -> bool:
    q = q.strip().lower()
    if not q:
        return True
    if q in user.full_name.lower() or (email is not None and q in email):
        return True
    # A phone search: only digits and the punctuation phone numbers are written with
    digits = "".join(ch for ch in q if ch.isdigit())
    return len(digits) >= 2 and all(ch.isdigit() or ch in " +-()" for ch in q) and digits in user.phone


async def staff_workers(session: AsyncSession, settings: Settings, staff: User, q: str = "") -> dict:
    """My workers: totals, then every worker assigned to the staff member, those owed money first (PAYS-1..4)."""
    ids = await people.workers_of(session, staff.id)
    users = await users_by_id(session, ids)
    money = await amounts.money_for(session, settings, ids)
    cash = (await amounts.cash_for(session, [staff.id]))[staff.id]
    rows = [_worker_row(users[i], money[i]) for i in ids if i in users]
    rows.sort(key=lambda r: (-r["remaining"], r["name"].lower()))
    return {
        "synced_at": iso(await doppi.last_updated(session)),
        "totals": {
            "workers": len(rows),
            "phones": sum(r["phones"] for r in rows),
            "remaining": sum(r["remaining"] for r in rows),
            "pending_review": sum(r["pending_review"] or 0 for r in rows),
            # time waiting for UseMinute's review is shown as time: it isn't money until accepted
            "pending_minutes": sum(r["minutes"][REC_PENDING] for r in rows),
            "awaiting": sum(r["awaiting"] for r in rows),
            "cash_on_hand": cash.on_hand,
            "available": cash.available,
        },
        "workers": [r for r in rows if _matches(users[r["id"]], q)],
    }


async def worker_detail(session: AsyncSession, settings: Settings, worker: User, viewer: User) -> dict:
    """A worker as their staff member sees them: the phone card of My Progress and the payment history (PAYS-2)."""
    data = await my_money(session, settings, worker, viewer)
    staff = data.pop("staff")
    cash = (await amounts.cash_for(session, [viewer.id]))[viewer.id]
    return {
        **data,
        "user": contact(worker),
        "blocked": worker.is_blocked,
        "staff": person(await session.get(User, staff["id"])) if staff else None,
        "can_pay": bool(staff) and staff["id"] == viewer.id and not worker.is_blocked,
        "available": cash.available,
    }


async def my_funds(session: AsyncSession, settings: Settings, staff: User, viewer: User) -> dict:
    """The staff member's cash position, funding awaiting their answer, and the statement (PAYS-11..13)."""
    cash = (await amounts.cash_for(session, [staff.id]))[staff.id]
    lines = await transfers.statement(session, staff.id)
    users = await users_by_id(session, {x.transfer.sender_id for x in lines} | {x.transfer.receiver_id for x in lines})
    statement = [
        {**transfer_out(x.transfer, users, viewer, settings), "incoming": x.incoming, "balance_after": x.balance_after}
        for x in lines[:LIST_LIMIT]
    ]
    return {
        "position": {
            "received": cash.received,
            "paid_out": cash.paid_out,
            "cash_on_hand": cash.on_hand,
            "funding_awaiting": cash.funding_awaiting,
            "payments_awaiting": cash.payments_awaiting,
            "available": cash.available,
        },
        "awaiting": [x for x in statement if x["incoming"] and x["state"] == AWAITING],
        "statement": statement,
        "statement_total": len(lines),
    }


# ---- Payments: admin view (section 8.4)


async def _assignments(session: AsyncSession) -> dict[int, int]:
    """worker id → their current staff member's id, for everyone."""
    q = select(StaffAssignment.worker_id, StaffAssignment.staff_id).where(StaffAssignment.ended_at.is_(None))
    return dict((await session.execute(q)).all())


async def _linked_emails(session: AsyncSession) -> dict[int, str]:
    q = select(DoppiLink.user_id, DoppiLink.email).where(DoppiLink.unlinked_at.is_(None))
    return dict((await session.execute(q)).all())


async def admin_staff(session: AsyncSession, settings: Settings) -> dict:
    """Company totals, every staff member sorted by need, and workers nobody can pay (PAYA-1..3, PAYA-7)."""
    staff = list(await session.scalars(select(User).where(roles.staff_condition(settings))))
    assigned = await _assignments(session)
    linked = await _linked_emails(session)
    unassigned_ids = [uid for uid in linked if uid not in assigned]
    money = await amounts.money_for(session, settings, set(assigned) | set(unassigned_ids))
    cash = await amounts.cash_for(session, [s.id for s in staff])
    workers_of: dict[int, list[int]] = {}
    for worker_id, staff_id in assigned.items():
        workers_of.setdefault(staff_id, []).append(worker_id)

    rows = []
    for s in staff:
        mine = [money[w] for w in workers_of.get(s.id, [])]
        to_be_paid = sum(m.remaining for m in mine)
        c = cash[s.id]
        rows.append({
            "id": s.id,
            "name": s.full_name,
            "role": roles.role_of(s, settings),
            "blocked": s.is_blocked,
            "workers": len(mine),
            "phones": sum(m.phones for m in mine),
            "to_be_paid": to_be_paid,
            "pending_review": sum(m.pending_review or 0 for m in mine),
            "pending_minutes": sum(m.split.minutes(REC_PENDING) for m in mine),
            "cash_on_hand": c.on_hand,
            "funding_awaiting": c.funding_awaiting,
            "needs_funding": amounts.needs_funding(to_be_paid, c),
        })
    rows.sort(key=lambda r: (-r["needs_funding"], -r["to_be_paid"], r["name"].lower()))
    unassigned = await users_by_id(session, unassigned_ids)
    return {
        "synced_at": iso(await doppi.last_updated(session)),
        "totals": {
            "staff": len(rows),
            "workers": sum(r["workers"] for r in rows),
            "phones": sum(r["phones"] for r in rows),
            "to_be_paid": sum(r["to_be_paid"] for r in rows),
            "pending_review": sum(r["pending_review"] for r in rows),
            "pending_minutes": sum(r["pending_minutes"] for r in rows),
            "staff_cash": sum(r["cash_on_hand"] for r in rows),
            "needs_funding": sum(r["needs_funding"] for r in rows),
        },
        "staff": rows,
        # PAYA-7: a phone but no responsible staff member, so nobody can pay them
        "unassigned": sorted(
            ({"id": u.id, "name": u.full_name, "phone_masked": formats.mask_phone(u.phone), "remaining": money[u.id].remaining}
             for u in unassigned.values()),
            key=lambda r: (-r["remaining"], r["name"].lower()),
        ),
    }


async def given(session: AsyncSession, settings: Settings, admin: User) -> dict:
    """PAYA-6: the funding this Admin sent, newest first, with each one's status."""
    rows = await transfers.sent(session, admin.id, FUNDING, LIST_LIMIT)
    return {"given": await transfers_out(session, rows, admin, settings)}


# ---- Users (section 6)


async def people_list(session: AsyncSession, settings: Settings, *, tab: str = "workers", q: str = "",
                      flt: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """The Workers list (everyone registered) or the Staff list (Staff and Admins), with search and the
    quick filters. Rows needing attention come first (USR-1..6)."""
    everyone = list(await session.scalars(select(User)))
    assigned = await _assignments(session)
    linked = await _linked_emails(session)
    staff_ids = {u.id for u in everyone if roles.is_staff(u, settings)}
    by_id = {u.id: u for u in everyone}
    counts = {"workers": len(everyone), "staff": len(staff_ids)}
    synced_at = iso(await doppi.last_updated(session))

    if tab == "staff":
        workers_of: dict[int, list[int]] = {}
        for worker_id, staff_id in assigned.items():
            workers_of.setdefault(staff_id, []).append(worker_id)
        money = await amounts.money_for(session, settings, set(assigned))
        rows = []
        for u in everyone:
            if u.id not in staff_ids or not _matches(u, q):
                continue
            mine = [money[w] for w in workers_of.get(u.id, [])]
            rows.append({
                "id": u.id,
                "name": u.full_name,
                "phone_masked": formats.mask_phone(u.phone),
                "role": roles.role_of(u, settings),
                "blocked": u.is_blocked,
                "workers": len(mine),
                "phones": sum(m.phones for m in mine),
                "remaining": sum(m.remaining for m in mine),
            })
        rows.sort(key=lambda r: r["name"].lower())
        return {"tab": "staff", "counts": counts, "total": len(rows), "rows": rows[offset : offset + limit], "synced_at": synced_at}

    money = await amounts.money_for(session, settings, [u.id for u in everyone])
    rows, filters = [], {"not_linked": 0, "no_staff": 0, "owed": 0, "blocked": 0}
    for u in everyone:
        role = roles.role_of(u, settings)
        email = linked.get(u.id)
        staff = by_id.get(assigned.get(u.id))
        # Only people expected to record need a phone; only people with a phone (or workers) need a staff member
        flags = {
            "not_linked": email is None and role == roles.Role.WORKER,
            "no_staff": staff is None and (email is not None or role == roles.Role.WORKER),
            "owed": money[u.id].remaining > 0,
            "blocked": u.is_blocked,
        }
        for name, on in flags.items():
            filters[name] += on
        if (flt and not flags.get(flt, False)) or not _matches(u, q, email):
            continue
        rows.append({
            "id": u.id,
            "name": u.full_name,
            "phone_masked": formats.mask_phone(u.phone),
            "role": role,
            "blocked": u.is_blocked,
            "linked": email is not None,
            "phones": 1 if email else 0,
            "not_linked": flags["not_linked"],
            "no_staff": flags["no_staff"],
            "staff": person(staff),
            "remaining": money[u.id].remaining,
            "overpaid": money[u.id].overpaid,
        })
    rows.sort(key=lambda r: (not (r["not_linked"] or r["no_staff"]), r["name"].lower()))
    return {"tab": "workers", "counts": counts, "filters": filters, "total": len(rows),
            "rows": rows[offset : offset + limit], "synced_at": synced_at}


async def profile(session: AsyncSession, settings: Settings, target: User, viewer: User) -> dict:
    """A person's profile with the actions this viewer may take (USR-7, USR-8)."""
    role = roles.role_of(target, settings)
    link = await session.scalar(select(DoppiLink).where(DoppiLink.user_id == target.id, DoppiLink.unlinked_at.is_(None)))
    money = (await amounts.money_for(session, settings, [target.id]))[target.id]
    staff = await staff_of(session, target.id)
    viewer_super = roles.is_super_admin(viewer, settings)
    viewer_admin = roles.is_admin(viewer, settings)
    other = target.id != viewer.id
    # ROLE-3: an Admin can't change another Admin or a Super Admin
    open_to_viewer = viewer_admin and (viewer_super or not other or not roles.is_admin(target, settings))
    workers = await session.scalar(
        select(func.count()).select_from(StaffAssignment).where(StaffAssignment.staff_id == target.id, StaffAssignment.ended_at.is_(None))
    )
    return {
        "user": {
            "id": target.id,
            "name": target.full_name,
            "phone": target.phone,
            "phone_verified": target.phone_verified,  # ONB-5: shared through Telegram, or typed
            "username": target.username,
            "registered_at": iso(target.registered_at),
            "role": role,
            "blocked": target.is_blocked,
            "language": target.language,
        },
        "email": link.email if link else None,
        "linked_at": iso(link.verified_at) if link else None,
        "staff": person(staff),
        "workers": int(workers or 0),
        "summary": money_out(money),
        "actions": {
            "make_staff": open_to_viewer and other and role == roles.Role.WORKER,
            "remove_staff": open_to_viewer and other and role == roles.Role.STAFF,
            "make_admin": viewer_super and other and role in (roles.Role.WORKER, roles.Role.STAFF),
            "remove_admin": viewer_super and other and role == roles.Role.ADMIN,
            "assign_staff": open_to_viewer,
            "block": open_to_viewer and other and role != roles.Role.SUPER_ADMIN,
            "edit": viewer_super,
            "unlink": open_to_viewer and link is not None,
        },
    }
