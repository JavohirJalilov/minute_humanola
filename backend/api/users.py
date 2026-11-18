"""The Users section and Settings (sections 6 and 10). Admins and the Super Admin only (APP-2)."""

import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import jobs
from backend.deps import AdminUser, SessionDep, SettingsDep
from backend.errors import AppError
from backend.models import AuditLog, User
from backend import roles
from backend.services import codes, doppi, overview, people, rates

router = APIRouter(prefix="/api", tags=["users"])

FILTERS = ("not_linked", "no_staff", "owed", "blocked")


class RoleIn(BaseModel):
    role: str
    move_workers_to: int | None = None


class StaffIn(BaseModel):
    staff_id: int | None = None


class BlockIn(BaseModel):
    blocked: bool


class EditIn(BaseModel):
    full_name: str | None = None
    phone: str | None = None


class UnlinkIn(BaseModel):
    keep_recordings: bool = True


class CodeIn(BaseModel):
    code: str | None = None  # empty: generate one
    label: str = Field("", max_length=100)
    staff_id: int | None = None


class CodePatch(BaseModel):
    label: str | None = None
    staff_id: int | None = None
    is_active: bool | None = None


class RateIn(BaseModel):
    scope: str = "company"
    user_id: int | None = None
    amount: int | None = None
    start_date: date | None = None  # today by default


# ---- people


@router.get("/users")
async def users(
    user: AdminUser, session: SessionDep, settings: SettingsDep,
    tab: str = "workers", q: str = "", filter: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> dict:
    return await overview.people_list(session, settings, tab=tab, q=q, flt=filter if filter in FILTERS else None,
                                      limit=limit, offset=offset)


@router.get("/staff-options")
async def staff_options(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    """Who a worker can be assigned to."""
    return {"staff": [overview.person(u) for u in await roles.staff_options(session, settings)]}


@router.get("/users/{user_id}")
async def profile(user_id: int, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await overview.profile(session, settings, await people.get_user(session, user_id), user)


@router.post("/users/{user_id}/role")
async def set_role(user_id: int, body: RoleIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    change = await people.set_role(session, settings, user, await people.get_user(session, user_id), body.role, body.move_workers_to)
    return {"role": change.new, "moved_workers": len(change.moved_workers)}


@router.post("/users/{user_id}/staff")
async def assign_staff(user_id: int, body: StaffIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    old, new = await people.assign_staff(session, settings, user, await people.get_user(session, user_id), body.staff_id)
    return {"staff_id": new, "previous_staff_id": old}


@router.post("/users/{user_id}/block")
async def block(user_id: int, body: BlockIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    await people.set_blocked(session, settings, user, await people.get_user(session, user_id), body.blocked)
    return {"blocked": body.blocked}


@router.patch("/users/{user_id}")
async def edit(user_id: int, body: EditIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    target = await people.get_user(session, user_id)
    await people.edit_user(session, settings, user, target, body.full_name, body.phone)
    return {"name": target.full_name, "phone": target.phone}


@router.post("/users/{user_id}/unlink")
async def unlink(user_id: int, body: UnlinkIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    email = await people.unlink(session, settings, user, await people.get_user(session, user_id), body.keep_recordings)
    return {"email": email}


# ---- settings (section 10)


async def _history(session, limit: int = 100) -> list[dict]:
    """SET-16: every change in Settings with who made it and when."""
    q = select(AuditLog).where(AuditLog.action.like("settings.%")).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
    rows = list(await session.scalars(q))
    users = await overview.users_by_id(session, {r.actor_id for r in rows} | {r.target_user_id for r in rows})
    return [
        {"at": r.at.isoformat(), "action": r.action, "by": overview.person(users.get(r.actor_id)),
         "about": overview.person(users.get(r.target_user_id)), "details": r.details}
        for r in rows
    ]


@router.get("/settings")
async def settings_page(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    return {
        "invite_codes": await codes.listing(session),
        "rates": await rates.listing(session, settings),
        # SET-15: the UseMinute login is never shown or editable here
        "history": await _history(session),
        "today": settings.today().isoformat(),
    }


@router.post("/settings/invite-codes")
async def add_code(body: CodeIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    invite = await codes.create(session, settings, user, code=body.code, label=body.label, staff_id=body.staff_id)
    return {"id": invite.id, "code": invite.code}


@router.patch("/settings/invite-codes/{code_id}")
async def change_code(code_id: int, body: CodePatch, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    given = body.model_fields_set
    invite = await codes.change(
        session, settings, user, code_id,
        label=body.label if "label" in given else codes.UNSET,
        staff_id=body.staff_id if "staff_id" in given else codes.UNSET,
        is_active=body.is_active if "is_active" in given and body.is_active is not None else codes.UNSET,
    )
    return {"id": invite.id, "is_active": invite.is_active}


@router.delete("/settings/invite-codes/{code_id}")
async def delete_code(code_id: int, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    await codes.delete(session, settings, user, code_id)
    return {"deleted": True}


@router.post("/settings/rates")
async def add_rate(body: RateIn, user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    change = await rates.add(session, settings, user, scope=body.scope, user_id=body.user_id, amount=body.amount,
                             start_date=body.start_date or settings.today())
    return {"id": change.rate.id, "affected": len(change.affected)}


@router.get("/useminute")
async def useminute(
    user: AdminUser, session: SessionDep, settings: SettingsDep, q: str = "", filter: str = "",
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """The UseMinute section: the sync, and every account on UseMinute with the worker who linked it."""
    return {"sync": await doppi.status(session, settings),
            **await doppi.accounts(session, q=q, flt=filter or None, offset=offset, limit=limit)}


@router.post("/settings/sync")
async def sync_now(user: AdminUser, session: SessionDep, settings: SettingsDep) -> dict:
    """SET-13: start a sync. A second press while one is running is refused with a message."""
    if not settings.doppi_configured:
        raise AppError("sync_not_configured", "UseMinute sync is not set up on the server", 409)
    if await doppi.is_running(session):
        raise AppError("sync_running", "A sync is already running", 409)
    await session.commit()
    task = asyncio.create_task(jobs.start_sync(settings))
    _syncs.add(task)
    task.add_done_callback(_syncs.discard)
    return {"started": True}


_syncs: set[asyncio.Task] = set()  # keeps started syncs alive until they finish
