"""The payment lifecycle (section 9).

Money is handed over twice, Admin → Staff (funding) and Staff → Worker (payment). The sender records the
handover; it counts only after the receiver confirms it. A transfer is never edited or deleted: its state
moves awaiting → accepted | declined | cancelled, and accepted → voided by a Super Admin.

Every function here runs in a write session, which SQLite gives to one caller at a time. So when two
taps arrive together, the second one sees what the first did: one answer is saved and the other is told
it was already answered (PAY-5), and a double tap on Send makes one transfer (PAYS-10, NFR-8).
The bot messages that announce a change are queued in the same transaction.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, roles
from backend.config import Settings
from backend.errors import AppError, forbidden, not_found
from backend.models import ACCEPTED, AWAITING, CANCELLED, DECLINED, FUNDING, PAYMENT, VOIDED, StaffAssignment, Transfer, User
from backend.services import amounts, audit, notify

AMOUNT_MIN, AMOUNT_MAX = 1, 1_000_000_000
TEXT_MAX = 500


def check_amount(amount) -> int:
    """PAYS-7: a whole number between 1 and 1 000 000 000 so'm."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not AMOUNT_MIN <= amount <= AMOUNT_MAX:
        raise AppError("amount_invalid", "The amount must be a whole number between 1 and 1 000 000 000 so'm")
    return amount


def check_paid_on(paid_on: date, settings: Settings) -> date:
    if paid_on > settings.today():
        raise AppError("date_in_future", "The date paid can't be in the future")
    return paid_on


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())[:TEXT_MAX]


async def _by_client_key(session: AsyncSession, sender_id: int, client_key: str | None) -> Transfer | None:
    if not client_key:
        return None
    return await session.scalar(select(Transfer).where(Transfer.sender_id == sender_id, Transfer.client_key == client_key))


async def current_staff_id(session: AsyncSession, worker_id: int) -> int | None:
    return await session.scalar(
        select(StaffAssignment.staff_id).where(StaffAssignment.worker_id == worker_id, StaffAssignment.ended_at.is_(None))
    )


async def send_payment(
    session: AsyncSession,
    settings: Settings,
    sender: User,
    *,
    worker_id: int,
    amount: int,
    paid_on: date,
    note: str | None = None,
    client_key: str | None = None,
    confirm_advance: bool = False,
) -> tuple[Transfer, bool]:
    """A staff member records that they paid one of their workers. Returns the transfer and whether it
    is new (False: the same form was sent twice, PAYS-10). Commits."""
    if not roles.is_staff(sender, settings):
        raise forbidden("Only staff can send payments")
    check_amount(amount)
    check_paid_on(paid_on, settings)
    existing = await _by_client_key(session, sender.id, client_key)
    if existing is not None:
        return existing, False

    worker = await session.get(User, worker_id)
    if worker is None:
        raise not_found("This person was not found")
    if await current_staff_id(session, worker.id) != sender.id:
        # USR-10: a worker with no staff member can't be paid; staff pay only their own workers
        raise forbidden("You can only pay workers assigned to you", "not_your_worker")
    if worker.is_blocked:
        raise AppError("receiver_blocked", "This person's access is disabled, so they can't confirm a payment")

    money = (await amounts.money_for(session, settings, [worker.id]))[worker.id]
    if amount > money.payable and not confirm_advance:
        # PAYS-8: allowed as an advance, after a warning and a second confirmation
        raise AppError(
            "advance_confirmation",
            "This is more than the worker is owed. Confirm to send it as an advance.",
            409,
            remaining=money.remaining,
            awaiting=money.awaiting,
            payable=money.payable,
        )
    cash = (await amounts.cash_for(session, [sender.id]))[sender.id]
    if amount > cash.available:
        # PAYS-9: staff can only pay out money they have confirmed receiving
        raise AppError(
            "insufficient_cash",
            "You can pay out at most {available} so'm: the funding you confirmed, minus what you already paid or sent.",
            409,
            available=max(cash.available, 0),
        )

    transfer = Transfer(
        kind=PAYMENT, sender_id=sender.id, receiver_id=worker.id, amount=amount, paid_on=paid_on,
        note=clean_text(note), state=AWAITING, created_at=clock.now(), client_key=client_key or None,
    )
    session.add(transfer)
    await session.flush()
    audit.record(session, sender.id, "transfer.created", target_user_id=worker.id, entity="transfer", entity_id=transfer.id,
                 kind=PAYMENT, amount=amount, paid_on=paid_on, advance=amount > money.payable)
    await notify.transfer_created(session, settings, transfer)
    await session.commit()
    return transfer, True


async def give_funding(
    session: AsyncSession,
    settings: Settings,
    sender: User,
    *,
    staff_id: int,
    amount: int,
    paid_on: date,
    note: str | None = None,
    client_key: str | None = None,
) -> tuple[Transfer, bool]:
    """An Admin records that they gave money to a staff member. Commits."""
    if not roles.is_admin(sender, settings):
        raise forbidden("Only admins can give funding")
    check_amount(amount)
    check_paid_on(paid_on, settings)
    existing = await _by_client_key(session, sender.id, client_key)
    if existing is not None:
        return existing, False

    staff = await session.get(User, staff_id)
    if staff is None:
        raise not_found("This person was not found")
    if staff.id == sender.id:
        raise AppError("self_funding", "You can't give funding to yourself")  # PAYA-8
    if not roles.is_staff(staff, settings):
        raise AppError("not_staff", "Funding can only be given to a staff member")
    if staff.is_blocked:
        raise AppError("receiver_blocked", "This person's access is disabled, so they can't confirm a payment")

    transfer = Transfer(
        kind=FUNDING, sender_id=sender.id, receiver_id=staff.id, amount=amount, paid_on=paid_on,
        note=clean_text(note), state=AWAITING, created_at=clock.now(), client_key=client_key or None,
    )
    session.add(transfer)
    await session.flush()
    audit.record(session, sender.id, "transfer.created", target_user_id=staff.id, entity="transfer", entity_id=transfer.id,
                 kind=FUNDING, amount=amount, paid_on=paid_on)
    await notify.transfer_created(session, settings, transfer)
    await session.commit()
    return transfer, True


async def _get(session: AsyncSession, transfer_id: int) -> Transfer:
    transfer = await session.get(Transfer, transfer_id)
    if transfer is None:
        raise not_found("This transfer was not found")
    return transfer


def _already(transfer: Transfer) -> AppError:
    return AppError("already_answered", "This was already answered", 409, state=transfer.state)


async def answer(session: AsyncSession, settings: Settings, actor: User, transfer_id: int, accept: bool,
                 reason: str | None = None) -> Transfer:
    """The receiver says whether they got the money (PAY-3). Final, and given once (PAY-5). Commits."""
    transfer = await _get(session, transfer_id)
    if transfer.receiver_id != actor.id:
        raise forbidden("Only the receiver can answer this", "not_receiver")
    if actor.is_blocked:
        raise forbidden("Your access has been disabled", "blocked")  # PAY-11
    if transfer.state != AWAITING:
        raise _already(transfer)
    transfer.state = ACCEPTED if accept else DECLINED
    transfer.answered_at = clock.now()
    if not accept:
        transfer.reason = clean_text(reason) or None
    audit.record(session, actor.id, f"transfer.{transfer.state}", target_user_id=transfer.sender_id, entity="transfer",
                 entity_id=transfer.id, kind=transfer.kind, amount=transfer.amount, reason=transfer.reason)
    await notify.transfer_answered(session, settings, transfer)
    await session.commit()
    return transfer


async def cancel(session: AsyncSession, settings: Settings, actor: User, transfer_id: int) -> Transfer:
    """The sender withdraws a transfer that is still awaiting confirmation (PAY-4). Commits."""
    transfer = await _get(session, transfer_id)
    if transfer.sender_id != actor.id:
        raise forbidden("Only the sender can cancel this", "not_sender")
    if transfer.state != AWAITING:
        raise _already(transfer)
    transfer.state = CANCELLED
    transfer.cancelled_at = clock.now()
    audit.record(session, actor.id, "transfer.cancelled", target_user_id=transfer.receiver_id, entity="transfer",
                 entity_id=transfer.id, kind=transfer.kind, amount=transfer.amount)
    await notify.transfer_cancelled(session, settings, transfer)
    await session.commit()
    return transfer


async def void(session: AsyncSession, settings: Settings, actor: User, transfer_id: int, reason: str | None) -> Transfer:
    """A Super Admin reverses an accepted transfer made by mistake, with a written reason (PAY-8). Commits."""
    if not roles.is_super_admin(actor, settings):
        raise forbidden("Only the Super Admin can void a transfer", "super_admin_only")
    why = clean_text(reason)
    if not why:
        raise AppError("reason_required", "Write the reason for the void")
    transfer = await _get(session, transfer_id)
    if transfer.state != ACCEPTED:
        raise AppError("not_accepted", "Only an accepted transfer can be voided", 409, state=transfer.state)
    transfer.state = VOIDED
    transfer.voided_at = clock.now()
    transfer.voided_by_id = actor.id
    transfer.reason = why
    audit.record(session, actor.id, "transfer.voided", target_user_id=transfer.receiver_id, entity="transfer",
                 entity_id=transfer.id, kind=transfer.kind, amount=transfer.amount, reason=why, sender_id=transfer.sender_id)
    await notify.transfer_voided(session, settings, transfer)
    await session.commit()
    return transfer


# ---- reading


async def received(session: AsyncSession, user_id: int, kind: str, limit: int | None = None) -> list[Transfer]:
    """Transfers addressed to the user, newest first."""
    q = select(Transfer).where(Transfer.receiver_id == user_id, Transfer.kind == kind).order_by(Transfer.created_at.desc(), Transfer.id.desc())
    return list(await session.scalars(q.limit(limit) if limit else q))


async def sent(session: AsyncSession, user_id: int, kind: str, limit: int | None = None) -> list[Transfer]:
    """Transfers the user sent, newest first."""
    q = select(Transfer).where(Transfer.sender_id == user_id, Transfer.kind == kind).order_by(Transfer.created_at.desc(), Transfer.id.desc())
    return list(await session.scalars(q.limit(limit) if limit else q))


@dataclass
class StatementLine:
    transfer: Transfer
    incoming: bool  # funding received (True) or a payment sent (False)
    balance_after: int | None  # cash on hand after this line; None when the line doesn't count


async def statement(session: AsyncSession, staff_id: int) -> list[StatementLine]:
    """PAYS-13: every funding received and every worker payment sent, newest first, with the cash on
    hand after each line that counts. A line counts from the moment the receiver accepted it."""
    q = select(Transfer).where(
        or_(
            and_(Transfer.kind == FUNDING, Transfer.receiver_id == staff_id),
            and_(Transfer.kind == PAYMENT, Transfer.sender_id == staff_id),
        )
    )
    rows = list(await session.scalars(q))

    def when(t: Transfer):
        return (t.answered_at if t.state == ACCEPTED and t.answered_at else t.created_at, t.id)

    rows.sort(key=when)
    balance, lines = 0, []
    for t in rows:
        incoming = t.kind == FUNDING
        counts = t.state == ACCEPTED
        if counts:
            balance += t.amount if incoming else -t.amount
        lines.append(StatementLine(t, incoming, balance if counts else None))
    lines.reverse()
    return lines
