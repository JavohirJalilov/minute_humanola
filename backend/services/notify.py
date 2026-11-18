"""Bot messages (section 11): written whenever something needs a person's answer or changes their money or
access, in the receiver's language (NTF-1).

Each function adds messages to the outbox in the caller's transaction, so they are saved together with
the change they announce; the bot delivers them. Nothing here talks to Telegram.
"""

from collections import defaultdict
from datetime import date, timedelta
from html import escape

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock
from backend.config import Settings
from backend.models import (
    ACCEPTED,
    AWAITING,
    CANCELLED,
    DECLINED,
    PAYMENT,
    REC_REJECTED,
    VOIDED,
    DoppiLink,
    Recording,
    SyncState,
    Transfer,
    TransferMessage,
    User,
)
from backend.roles import Role
from backend import roles
from backend.services import formats, outbox
from backend.texts import t

REJECTIONS_SHOWN = 10
ALERT_AGAIN = timedelta(hours=24)


# ---- building blocks


def app_buttons(settings: Settings, lang: str) -> list | None:
    """The Open app button. Telegram accepts only HTTPS addresses for it."""
    if not settings.base_url.startswith("https://"):
        return None
    return [[{"text": t(lang, "📱 Open app"), "web_app": settings.webapp_url}]]


def answer_buttons(transfer: Transfer, lang: str) -> list:
    """The two buttons the receiver answers with (PAY-2)."""
    yes, no = ("✅ Accept", "⛔ Decline") if transfer.kind == PAYMENT else ("✅ Received", "⛔ Not received")
    return [[
        {"text": t(lang, yes), "data": f"tr:{transfer.id}:a"},
        {"text": t(lang, no), "data": f"tr:{transfer.id}:d"},
    ]]


def _contact(user: User) -> str:
    return f"<b>{escape(user.full_name)}</b>, {formats.pretty_phone(user.phone)}"


def transfer_body(transfer: Transfer, sender: User, lang: str) -> str:
    head = "💸 Payment of <b>{amount}</b> from {sender}" if transfer.kind == PAYMENT else "💼 Funding of <b>{amount}</b> from {sender}"
    lines = [
        t(lang, head, amount=formats.money(transfer.amount, lang), sender=escape(sender.full_name)),
        t(lang, "🗓 Date paid: {date}", date=formats.day(transfer.paid_on)),
    ]
    if transfer.note:
        lines.append(f"💬 {escape(transfer.note)}")
    return "\n".join(lines)


def _reason(transfer: Transfer, lang: str) -> str:
    return "\n" + t(lang, "Reason: {reason}", reason=escape(transfer.reason)) if transfer.reason else ""


def transfer_outcome(transfer: Transfer, lang: str) -> str:
    """The line under a transfer message: the question, or what became of it (NTF-3)."""
    if transfer.state == ACCEPTED:
        return t(lang, "✅ You confirmed: received")
    if transfer.state == DECLINED:
        return t(lang, "⛔ You answered: not received") + _reason(transfer, lang)
    if transfer.state == CANCELLED:
        return t(lang, "↩️ The sender cancelled this")
    if transfer.state == VOIDED:
        return t(lang, "🚫 Voided by the Super Admin: it no longer counts") + _reason(transfer, lang)
    return t(lang, "Did you receive this money?")


async def _parties(session: AsyncSession, transfer: Transfer) -> tuple[User, User]:
    return await session.get(User, transfer.sender_id), await session.get(User, transfer.receiver_id)


async def refresh_messages(session: AsyncSession, transfer: Transfer) -> None:
    """Bring every bot message that asked about this transfer in line with its state: the outcome is
    shown, and the buttons stay only while it still awaits an answer (PAY-6, NTF-3)."""
    sender, receiver = await _parties(session, transfer)
    lang = receiver.language
    text = transfer_body(transfer, sender, lang) + "\n\n" + transfer_outcome(transfer, lang)
    buttons = answer_buttons(transfer, lang) if transfer.state == AWAITING else None
    for m in await session.scalars(select(TransferMessage).where(TransferMessage.transfer_id == transfer.id)):
        outbox.edit(session, m.chat_id, m.message_id, text, buttons, purpose="transfer.update")


# ---- transfers


async def transfer_created(session: AsyncSession, settings: Settings, transfer: Transfer, reminder: bool = False) -> None:
    """Ask the receiver right away, with two buttons (PAY-2). Also the daily reminder (PAY-7)."""
    sender, receiver = await _parties(session, transfer)
    lang = receiver.language
    text = transfer_body(transfer, sender, lang) + "\n\n" + t(lang, "Did you receive this money?")
    if reminder:
        text = t(lang, "⏰ Reminder: this is still waiting for your answer.") + "\n\n" + text
    outbox.send(session, receiver, text, answer_buttons(transfer, lang), transfer_id=transfer.id,
                purpose="transfer.reminder" if reminder else "transfer.ask")


async def transfer_answered(session: AsyncSession, settings: Settings, transfer: Transfer) -> None:
    """Remove the buttons, tell the sender the answer (PAY-6), and tell the Admins about a decline (PAY-9)."""
    await refresh_messages(session, transfer)
    sender, receiver = await _parties(session, transfer)
    accepted = transfer.state == ACCEPTED
    lang = sender.language
    values = {"name": escape(receiver.full_name), "amount": formats.money(transfer.amount, lang)}
    if transfer.kind == PAYMENT:
        text = t(lang, "✅ {name} accepted your payment of <b>{amount}</b>", **values) if accepted else \
            t(lang, "⛔ {name} declined your payment of <b>{amount}</b>: they say they did not get it", **values)
    else:
        text = t(lang, "✅ {name} confirmed receiving <b>{amount}</b>", **values) if accepted else \
            t(lang, "⛔ {name} says they did <b>not</b> receive <b>{amount}</b>", **values)
    text += "\n" + t(lang, "🗓 Date paid: {date}", date=formats.day(transfer.paid_on)) + _reason(transfer, lang)
    outbox.send(session, sender, text, purpose="transfer.answer")
    if accepted:
        return
    for admin in await roles.admins(session, settings):
        if admin.id in (sender.id, receiver.id):
            continue
        lang = admin.language
        what = "⚠️ Declined payment: {receiver} says they did not get <b>{amount}</b> from {sender}" if transfer.kind == PAYMENT \
            else "⚠️ Declined funding: {receiver} says they did not get <b>{amount}</b> from {sender}"
        text = t(lang, what, receiver=escape(receiver.full_name), sender=escape(sender.full_name),
                 amount=formats.money(transfer.amount, lang))
        text += "\n" + t(lang, "🗓 Date paid: {date}", date=formats.day(transfer.paid_on)) + _reason(transfer, lang)
        outbox.send(session, admin, text, purpose="transfer.declined")


async def transfer_cancelled(session: AsyncSession, settings: Settings, transfer: Transfer) -> None:
    await refresh_messages(session, transfer)
    sender, receiver = await _parties(session, transfer)
    lang = receiver.language
    outbox.send(session, receiver,
                t(lang, "↩️ {sender} cancelled the transfer of <b>{amount}</b> ({date}). You don't need to answer it.",
                  sender=escape(sender.full_name), amount=formats.money(transfer.amount, lang), date=formats.day(transfer.paid_on)),
                purpose="transfer.cancelled")


async def transfer_voided(session: AsyncSession, settings: Settings, transfer: Transfer) -> None:
    """Both parties are told about a void (PAY-8)."""
    await refresh_messages(session, transfer)
    sender, receiver = await _parties(session, transfer)
    for person in (receiver, sender):
        lang = person.language
        text = t(lang, "🚫 The Super Admin voided the transfer of <b>{amount}</b> from {sender} to {receiver} ({date}). It no longer counts in any balance.",
                 amount=formats.money(transfer.amount, lang), sender=escape(sender.full_name),
                 receiver=escape(receiver.full_name), date=formats.day(transfer.paid_on))
        outbox.send(session, person, text + _reason(transfer, lang), purpose="transfer.voided")


# ---- people


def linked(session: AsyncSession, settings: Settings, user: User, email: str, found: int) -> None:
    """UseMinute account linked: the email and how many recordings were found."""
    lang = user.language
    text = t(lang, "✅ Your UseMinute account <b>{email}</b> is linked.", email=escape(email)) + "\n"
    text += t(lang, "Recordings found: <b>{count}</b>.", count=found) if found else \
        t(lang, "No recordings were found yet. They appear here after UseMinute receives them.")
    outbox.send(session, user, text, app_buttons(settings, lang), purpose="link.created")


def assigned(session: AsyncSession, worker: User, staff: User | None, old: User | None = None) -> None:
    """Worker assigned to a staff member, or moved: each is told the other's name and phone number."""
    if staff is not None:
        outbox.send(session, worker, t(worker.language, "👤 Your staff member is now {contact}. Ask them about your payments.", contact=_contact(staff)),
                    purpose="staff.assigned")
        outbox.send(session, staff, t(staff.language, "👷 {contact} is now your worker. You pay them in Payments → My workers.", contact=_contact(worker)),
                    purpose="staff.assigned")
    else:
        outbox.send(session, worker, t(worker.language, "👤 You have no responsible staff member now. An administrator will assign one."),
                    purpose="staff.unassigned")
    if old is not None and (staff is None or old.id != staff.id):
        outbox.send(session, old, t(old.language, "👷 {name} is no longer your worker.", name=f"<b>{escape(worker.full_name)}</b>"),
                    purpose="staff.unassigned")


def workers_moved(session: AsyncSession, workers: list[User], staff: User) -> None:
    """All workers of a staff member were moved to another in one step (USR-14)."""
    if not workers:
        return
    for worker in workers:
        outbox.send(session, worker, t(worker.language, "👤 Your staff member is now {contact}. Ask them about your payments.", contact=_contact(staff)),
                    purpose="staff.assigned")
    names = "\n".join(f"• {_contact(w)}" for w in workers[:30])
    if len(workers) > 30:
        names += "\n" + t(staff.language, "…and {count} more", count=len(workers) - 30)
    outbox.send(session, staff, t(staff.language, "👷 {count} workers were moved to you:", count=len(workers)) + "\n" + names,
                purpose="staff.assigned")


ROLE_NOTES = {
    Role.WORKER: "You see your own phone and money.",
    Role.STAFF: "You can now pay your workers: Payments → My workers. An Admin gives you the money first: Payments → My funds.",
    Role.ADMIN: "You can now give funding to staff (Payments → Staff) and manage people and settings (Users).",
}
ROLE_NAMES = {Role.WORKER: "Worker", Role.STAFF: "Staff", Role.ADMIN: "Admin", Role.SUPER_ADMIN: "Super Admin"}


def role_changed(session: AsyncSession, settings: Settings, user: User) -> None:
    """USR-9: a role change is announced to the affected user, with what the new role lets them do."""
    lang, role = user.language, Role(user.role)
    text = t(lang, "Your role is now <b>{role}</b>.", role=t(lang, ROLE_NAMES[role])) + "\n" + t(lang, ROLE_NOTES[role])
    outbox.send(session, user, text, app_buttons(settings, lang), purpose="user.role")


def access_changed(session: AsyncSession, settings: Settings, user: User) -> None:
    lang = user.language
    if user.is_blocked:
        outbox.send(session, user, t(lang, "⛔ Your access has been disabled. Contact an administrator."), purpose="user.blocked")
    else:
        outbox.send(session, user, t(lang, "✅ Your access has been restored."), app_buttons(settings, lang), purpose="user.unblocked")


async def rate_changed(session: AsyncSession, settings: Settings, affected: dict[int, int | None], start_date: date) -> None:
    """SET-11: the new rate and its start date, to the workers it affects."""
    if not affected:
        return
    for user in await session.scalars(select(User).where(User.is_blocked.is_(False))):
        if user.id not in affected:
            continue
        lang, amount = user.language, affected[user.id]
        if amount is None:
            text = t(lang, "ℹ️ From {date} no pay rate is set for you. Ask an administrator.", date=formats.day(start_date))
        else:
            text = t(lang, "ℹ️ Your pay rate from {date}: <b>{amount}</b> per hour of accepted recording.",
                     date=formats.day(start_date), amount=formats.money(amount, lang))
        outbox.send(session, user, text, purpose="rate.changed")


# ---- UseMinute


async def rejections(session: AsyncSession, settings: Settings) -> int:
    """Recording rejected by UseMinute: which phone, the date, the length and UseMinute's reason, as at most one
    summary message per worker per day. Returns how many workers are told."""
    today = settings.today()
    q = (
        select(Recording, DoppiLink.user_id)
        .join(DoppiLink, and_(DoppiLink.email == Recording.email, DoppiLink.unlinked_at.is_(None)))
        .where(Recording.status == REC_REJECTED, Recording.rejection_notified_at.is_(None))
        .order_by(Recording.recorded_at)
    )
    by_user: dict[int, list[Recording]] = defaultdict(list)
    for recording, user_id in (await session.execute(q)).all():
        by_user[user_id].append(recording)
    told = 0
    for user_id, recordings in by_user.items():
        user = await session.get(User, user_id)
        if user is None or user.rejection_notice_on == today:
            continue  # today's summary went out: these wait for tomorrow's
        lang = user.language
        lines = [t(lang, "⛔ UseMinute rejected {count} of your recordings on <b>{email}</b>:", count=len(recordings), email=escape(recordings[0].email))]
        for r in recordings[-REJECTIONS_SHOWN:]:
            reason = (r.rejection_reason or "").split("\n")[0][:150]
            line = f"• {formats.day(r.day)} · {formats.duration(r.duration_ms / 60000, lang)}"
            lines.append(line + (f" — {escape(reason)}" if reason else ""))
        if len(recordings) > REJECTIONS_SHOWN:
            lines.append(t(lang, "…and {count} more", count=len(recordings) - REJECTIONS_SHOWN))
        lines.append(t(lang, "Rejected time is not paid. The app shows every recording and its reason."))
        if not user.is_blocked:
            outbox.send(session, user, "\n".join(lines), app_buttons(settings, lang), purpose="recordings.rejected")
            told += 1
        user.rejection_notice_on = today
        ids = [r.id for r in recordings]
        for i in range(0, len(ids), 500):
            await session.execute(update(Recording).where(Recording.id.in_(ids[i : i + 500])).values(rejection_notified_at=clock.now()))
    return told


async def sync_failed(session: AsyncSession, settings: Settings, error: str) -> None:
    """UseMinute sync failed or the UseMinute login expired: the Super Admins get the error text (EXT-11). The same
    error is not repeated more than once a day."""
    state = await session.get(SyncState, 1)
    now = clock.now()
    if state is None or (state.alerted_error == error and state.alerted_at and now - state.alerted_at < ALERT_AGAIN):
        return
    state.alerted_error, state.alerted_at = error, now
    for admin in await roles.super_admins(session, settings):
        outbox.send(session, admin, t(admin.language, "⚠️ UseMinute sync failed:") + "\n" + escape(error), purpose="sync.failed")
