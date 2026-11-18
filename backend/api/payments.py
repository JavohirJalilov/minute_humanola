"""Payments (section 8) and the transfer lifecycle (section 9)."""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.deps import AdminUser, CurrentUser, SessionDep, SettingsDep, StaffUser, visible_user
from backend.errors import AppError
from backend.models import Transfer, User
from backend.services import export, formats, outbox, overview, people, transfers
from backend.texts import t

router = APIRouter(prefix="/api", tags=["payments"])


class PaymentIn(BaseModel):
    worker_id: int
    amount: int
    paid_on: date | None = None  # today by default
    note: str = Field("", max_length=500)
    client_key: str | None = Field(None, max_length=64)
    confirm_advance: bool = False


class FundingIn(BaseModel):
    staff_id: int
    amount: int
    paid_on: date | None = None
    note: str = Field("", max_length=500)
    client_key: str | None = Field(None, max_length=64)


class ReasonIn(BaseModel):
    reason: str = Field("", max_length=500)


def _brief(transfer: Transfer, created: bool = True) -> dict:
    return {"id": transfer.id, "state": transfer.state, "amount": transfer.amount, "created": created}


# ---- worker view (8.2)


@router.get("/payments/me")
async def my_money(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await overview.my_money(session, settings, user, user)


# ---- answering, cancelling, voiding (section 9)


@router.post("/transfers/{transfer_id}/accept")
async def accept(transfer_id: int, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return _brief(await transfers.answer(session, settings, user, transfer_id, True))


@router.post("/transfers/{transfer_id}/decline")
async def decline(transfer_id: int, body: ReasonIn, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return _brief(await transfers.answer(session, settings, user, transfer_id, False, body.reason))


@router.post("/transfers/{transfer_id}/cancel")
async def cancel(transfer_id: int, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return _brief(await transfers.cancel(session, settings, user, transfer_id))


@router.post("/transfers/{transfer_id}/void")
async def void(transfer_id: int, body: ReasonIn, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return _brief(await transfers.void(session, settings, user, transfer_id, body.reason))


# ---- staff view (8.3)


@router.get("/staff/workers")
async def my_workers(user: StaffUser, session: SessionDep, settings: SettingsDep, q: str = "") -> dict:
    return await overview.staff_workers(session, settings, user, q)


@router.get("/staff/workers/{worker_id}")
async def worker(worker_id: int, user: StaffUser, session: SessionDep, settings: SettingsDep) -> dict:
    target = await visible_user(session, settings, user, worker_id)
    return await overview.worker_detail(session, settings, target, user)


@router.post("/payments")
async def send_payment(body: PaymentIn, user: StaffUser, session: SessionDep, settings: SettingsDep) -> dict:
    transfer, created = await transfers.send_payment(
        session, settings, user, worker_id=body.worker_id, amount=body.amount, paid_on=body.paid_on or settings.today(),
        note=body.note, client_key=body.client_key, confirm_advance=body.confirm_advance,
    )
    return _brief(transfer, created)


@router.get("/staff/funds")
async def my_funds(user: StaffUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await overview.my_funds(session, settings, user, user)


# ---- admin view (8.4)


@router.get("/admin/staff")
async def staff(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await overview.admin_staff(session, settings)


async def _staff_member(session, user_id: int) -> User:
    return await people.get_user(session, user_id)


@router.get("/admin/staff/{staff_id}")
async def staff_member(staff_id: int, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    """PAYA-4: a staff member's workers, phones and statement exactly as they see them, read-only."""
    target = await _staff_member(session, staff_id)
    return {
        "staff": overview.contact(target),
        "workers": await overview.staff_workers(session, settings, target),
        "funds": await overview.my_funds(session, settings, target, user),
    }


@router.post("/fundings")
async def give_funding(body: FundingIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    transfer, created = await transfers.give_funding(
        session, settings, user, staff_id=body.staff_id, amount=body.amount, paid_on=body.paid_on or settings.today(),
        note=body.note, client_key=body.client_key,
    )
    return _brief(transfer, created)


@router.get("/admin/given")
async def given(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await overview.given(session, settings, user)


@router.post("/admin/export/staff")
async def export_staff(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    """PAYA-9: the staff table as an Excel file, which the bot sends to the Admin's chat."""
    content = await export.staff_table(session, settings, user.language)
    today = formats.day(settings.today())
    outbox.document(session, user, content, f"staff-{settings.today():%Y%m%d}.xlsx",
                    t(user.language, "📎 Staff table, {date}", date=today), purpose="export.staff")
    await session.commit()
    return {"sent": True}


@router.post("/admin/export/statement/{staff_id}")
async def export_statement(staff_id: int, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    target = await _staff_member(session, staff_id)
    content = await export.statement(session, settings, target, user.language)
    if not content:
        raise AppError("export_failed", "The file could not be made")
    outbox.document(session, user, content, f"statement-{target.id}-{settings.today():%Y%m%d}.xlsx",
                    t(user.language, "📎 Statement of {name}, {date}", name=target.full_name, date=formats.day(settings.today())),
                    purpose="export.statement")
    await session.commit()
    return {"sent": True}
