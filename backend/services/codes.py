"""Invite codes (section 10.1): the codes a worker must enter before linking a UseMinute account."""

import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, roles
from backend.config import Settings
from backend.errors import AppError, not_found
from backend.models import DoppiLink, InviteCode, LinkProgress, User
from backend.services import audit, people
from backend.services.linking import INVITE_FORMAT, normalize_invite

# Generated codes leave out the look-alikes 0/O and 1/I
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LABEL_MAX = 100
UNSET = object()


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


async def _owner(session: AsyncSession, settings: Settings, staff_id: int | None) -> int | None:
    if staff_id is None:
        return None
    staff = await session.get(User, staff_id)
    if staff is None or not roles.is_staff(staff, settings):
        raise AppError("not_staff", "This person is not a staff member")
    return staff.id


async def create(session: AsyncSession, settings: Settings, actor: User, *, code: str | None = None,
                 label: str | None = None, staff_id: int | None = None) -> InviteCode:
    """SET-2: typed by the Admin or generated; 8 Latin capital letters and digits; unique. Commits."""
    people.require_admin(actor, settings)
    if code:
        value = normalize_invite(code)
        if not INVITE_FORMAT.match(value):
            raise AppError("code_format", "A code is 8 characters: Latin letters and digits")
        if await session.scalar(select(InviteCode.id).where(InviteCode.code == value)):
            raise AppError("code_exists", "This code already exists", 409)
    else:
        while True:
            value = generate()
            if not await session.scalar(select(InviteCode.id).where(InviteCode.code == value)):
                break
    invite = InviteCode(code=value, label=" ".join((label or "").split())[:LABEL_MAX],
                        staff_id=await _owner(session, settings, staff_id), is_active=True,
                        created_by_id=actor.id, created_at=clock.now())
    session.add(invite)
    await session.flush()
    audit.record(session, actor.id, "settings.invite_code.created", entity="invite_code", entity_id=invite.id,
                 code=value, label=invite.label, staff_id=invite.staff_id)
    await session.commit()
    return invite


async def change(session: AsyncSession, settings: Settings, actor: User, code_id: int, *, label=UNSET,
                 staff_id=UNSET, is_active=UNSET) -> InviteCode:
    """Rename, give to a staff member, or switch on and off (SET-4: workers already linked stay linked). Commits."""
    people.require_admin(actor, settings)
    invite = await session.get(InviteCode, code_id)
    if invite is None:
        raise not_found("This invite code was not found")
    changes = {}
    if label is not UNSET:
        new = " ".join((label or "").split())[:LABEL_MAX]
        if new != invite.label:
            changes["label"] = [invite.label, new]
            invite.label = new
    if staff_id is not UNSET:
        new = await _owner(session, settings, staff_id)
        if new != invite.staff_id:
            changes["staff_id"] = [invite.staff_id, new]
            invite.staff_id = new
    if is_active is not UNSET and bool(is_active) != invite.is_active:
        changes["is_active"] = [invite.is_active, bool(is_active)]
        invite.is_active = bool(is_active)
    if changes:
        action = "settings.invite_code.changed"
        if set(changes) == {"is_active"}:
            action = "settings.invite_code.activated" if invite.is_active else "settings.invite_code.deactivated"
        audit.record(session, actor.id, action, entity="invite_code", entity_id=invite.id, code=invite.code, changes=changes)
        await session.commit()
    return invite


async def uses(session: AsyncSession) -> dict[int, int]:
    """invite code id → how many workers have used it (linked with it, now or earlier)."""
    q = (
        select(DoppiLink.invite_code_id, func.count(func.distinct(DoppiLink.user_id)))
        .where(DoppiLink.invite_code_id.is_not(None))
        .group_by(DoppiLink.invite_code_id)
    )
    return {code_id: n for code_id, n in (await session.execute(q)).all()}


async def delete(session: AsyncSession, settings: Settings, actor: User, code_id: int) -> None:
    """SET-5: a code that has been used cannot be deleted, only deactivated. Commits."""
    people.require_admin(actor, settings)
    invite = await session.get(InviteCode, code_id)
    if invite is None:
        raise not_found("This invite code was not found")
    used = await session.scalar(select(DoppiLink.id).where(DoppiLink.invite_code_id == code_id).limit(1))
    used = used or await session.scalar(select(LinkProgress.user_id).where(LinkProgress.invite_code_id == code_id).limit(1))
    if used:
        raise AppError("code_used", "A code that has been used can't be deleted, only switched off", 409)
    audit.record(session, actor.id, "settings.invite_code.deleted", entity="invite_code", entity_id=invite.id,
                 code=invite.code, label=invite.label)
    await session.delete(invite)
    await session.commit()


async def listing(session: AsyncSession) -> list[dict]:
    """SET-1: each code with its label, staff member, switch, creator, creation time and number of uses."""
    counts = await uses(session)
    invites = list(await session.scalars(select(InviteCode).order_by(InviteCode.is_active.desc(), InviteCode.created_at.desc())))
    ids = {i.staff_id for i in invites} | {i.created_by_id for i in invites}
    names = {}
    if ids - {None}:
        names = dict((await session.execute(select(User.id, User.full_name).where(User.id.in_(ids - {None})))).all())
    return [
        {
            "id": i.id,
            "code": i.code,
            "label": i.label,
            "staff": {"id": i.staff_id, "name": names.get(i.staff_id, "")} if i.staff_id else None,
            "is_active": i.is_active,
            "created_by": names.get(i.created_by_id),
            "created_at": i.created_at.isoformat(),
            "uses": counts.get(i.id, 0),
        }
        for i in invites
    ]
