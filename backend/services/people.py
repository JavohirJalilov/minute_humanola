"""Managing people (section 6): roles, responsible staff, blocking, corrections and unlinking.

The permission matrix of section 3 is enforced here, on the server, for every call (ROLE-5). Each change
is written to the audit log and announced to the people it concerns in the same transaction.
"""

from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, roles
from backend.config import Settings
from backend.errors import AppError, forbidden, not_found
from backend.models import DoppiLink, InviteCode, StaffAssignment, User
from backend.roles import Role
from backend.services import audit, notify, registration


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("This person was not found")
    return user


def require_admin(actor: User, settings: Settings) -> None:
    if not roles.is_admin(actor, settings):
        raise forbidden("Admins only", "admins_only")


def _guard_target(actor: User, target: User, settings: Settings) -> None:
    """ROLE-3: an Admin cannot change, block or demote another Admin or a Super Admin."""
    if target.id == actor.id or roles.is_super_admin(actor, settings):
        return
    if roles.is_admin(target, settings):
        raise forbidden("Only the Super Admin can change an Admin", "admin_protected")


async def current_assignment(session: AsyncSession, worker_id: int) -> StaffAssignment | None:
    return await session.scalar(
        select(StaffAssignment).where(StaffAssignment.worker_id == worker_id, StaffAssignment.ended_at.is_(None))
    )


async def workers_of(session: AsyncSession, staff_id: int) -> list[int]:
    """Ids of the workers currently assigned to a staff member (USR-12)."""
    q = select(StaffAssignment.worker_id).where(StaffAssignment.staff_id == staff_id, StaffAssignment.ended_at.is_(None))
    return list(await session.scalars(q))


async def _staff_target(session: AsyncSession, settings: Settings, staff_id: int) -> User:
    staff = await session.get(User, staff_id)
    if staff is None:
        raise not_found("This staff member was not found")
    if not roles.is_staff(staff, settings):
        raise AppError("not_staff", "This person is not a staff member")
    if staff.is_blocked:
        raise AppError("staff_blocked", "This staff member's access is disabled")
    return staff


async def assign_staff(session: AsyncSession, settings: Settings, actor: User, worker: User, staff_id: int | None) -> tuple[int | None, int | None]:
    """Set, change or clear the staff member responsible for paying a worker (USR-10). History is kept:
    payments already made stay with the staff member who made them (USR-13). Returns (old, new). Commits."""
    require_admin(actor, settings)
    _guard_target(actor, worker, settings)
    staff = None
    if staff_id is not None:
        if staff_id == worker.id:
            raise AppError("own_staff", "A person can't be their own responsible staff member")  # USR-15
        staff = await _staff_target(session, settings, staff_id)
    current = await current_assignment(session, worker.id)
    old_id = current.staff_id if current else None
    if old_id == staff_id:
        return old_id, staff_id
    now = clock.now()
    if current is not None:
        current.ended_at = now
        await session.flush()
    if staff is not None:
        session.add(StaffAssignment(worker_id=worker.id, staff_id=staff.id, started_at=now, set_by_id=actor.id, source="admin"))
    audit.record(session, actor.id, "staff.assigned" if staff else "staff.unassigned", target_user_id=worker.id,
                 entity="user", entity_id=worker.id, staff_id=staff_id, previous_staff_id=old_id, source="admin")
    notify.assigned(session, worker, staff, await session.get(User, old_id) if old_id else None)
    await session.commit()
    return old_id, staff_id


@dataclass
class RoleChange:
    old: str
    new: str
    moved_workers: list[int] = field(default_factory=list)
    moved_to: int | None = None


# (from, to) → who may do it. Admin → Worker is two steps: Remove Admin, then Remove Staff
TRANSITIONS = {
    (Role.WORKER, Role.STAFF): Role.ADMIN,  # Make Staff
    (Role.STAFF, Role.WORKER): Role.ADMIN,  # Remove Staff
    (Role.WORKER, Role.ADMIN): Role.SUPER_ADMIN,  # Make Admin
    (Role.STAFF, Role.ADMIN): Role.SUPER_ADMIN,  # Make Admin
    (Role.ADMIN, Role.STAFF): Role.SUPER_ADMIN,  # Remove Admin
}


async def set_role(session: AsyncSession, settings: Settings, actor: User, target: User, new_role: str,
                   move_workers_to: int | None = None) -> RoleChange:
    """Make Staff, Remove Staff, Make Admin, Remove Admin (USR-8). Commits."""
    require_admin(actor, settings)
    if target.id == actor.id:
        raise forbidden("You can't change your own role", "own_role")  # ROLE-2
    if roles.is_super_admin(target, settings):
        raise forbidden("A Super Admin's role is set in the server configuration", "super_admin_fixed")
    _guard_target(actor, target, settings)
    needed = TRANSITIONS.get((Role(target.role), Role(new_role))) if new_role in roles.STORED_ROLES else None
    if needed is None:
        raise AppError("role_change_invalid", "This role change is not possible")
    if roles.rank_of(actor, settings) < roles.RANK[needed]:
        raise forbidden("Only the Super Admin can add or remove Admins", "super_admin_only")

    change = RoleChange(old=target.role, new=new_role)
    if new_role == Role.WORKER:
        # USR-14: refused while they still have workers, unless all are moved in the same step
        workers = await workers_of(session, target.id)
        if workers:
            if move_workers_to is None:
                raise AppError("has_workers", "This staff member still has {count} workers. Move them to another staff member first.",
                               409, count=len(workers))
            if move_workers_to == target.id:
                raise AppError("not_staff", "Choose another staff member")
            destination = await _staff_target(session, settings, move_workers_to)
            now = clock.now()
            await session.execute(
                update(StaffAssignment).where(StaffAssignment.staff_id == target.id, StaffAssignment.ended_at.is_(None)).values(ended_at=now)
            )
            moved = []
            for worker_id in workers:
                if worker_id == destination.id:
                    continue  # they can't be their own staff member: left without one
                session.add(StaffAssignment(worker_id=worker_id, staff_id=destination.id, started_at=now, set_by_id=actor.id, source="admin"))
                audit.record(session, actor.id, "staff.assigned", target_user_id=worker_id, entity="user", entity_id=worker_id,
                             staff_id=destination.id, previous_staff_id=target.id, source="move_all")
                moved.append(worker_id)
            change.moved_workers, change.moved_to = moved, destination.id
            if moved:
                people_moved = list(await session.scalars(select(User).where(User.id.in_(moved)).order_by(User.full_name)))
                notify.workers_moved(session, people_moved, destination)
        # Their invite codes stop assigning new workers to them
        await session.execute(update(InviteCode).where(InviteCode.staff_id == target.id).values(staff_id=None))
    target.role = new_role
    audit.record(session, actor.id, "user.role_changed", target_user_id=target.id, entity="user", entity_id=target.id,
                 old=change.old, new=new_role)
    notify.role_changed(session, settings, target)
    await session.commit()
    return change


async def set_blocked(session: AsyncSession, settings: Settings, actor: User, target: User, blocked: bool) -> bool:
    """Disable or restore access; data and payment history are kept (ROLE-6). Returns whether it changed. Commits."""
    require_admin(actor, settings)
    if target.id == actor.id:
        raise forbidden("You can't block yourself", "own_block")  # ROLE-2
    if roles.is_super_admin(target, settings):
        raise forbidden("A Super Admin can't be blocked", "super_admin_fixed")
    _guard_target(actor, target, settings)
    if target.is_blocked == blocked:
        return False
    target.is_blocked = blocked
    audit.record(session, actor.id, "user.blocked" if blocked else "user.unblocked", target_user_id=target.id,
                 entity="user", entity_id=target.id)
    notify.access_changed(session, settings, target)
    await session.commit()
    return True


async def edit_user(session: AsyncSession, settings: Settings, actor: User, target: User,
                    full_name: str | None = None, phone: str | None = None) -> None:
    """Correct registration data (Super Admin only). Commits."""
    if not roles.is_super_admin(actor, settings):
        raise forbidden("Only the Super Admin can edit a person's name or phone number", "super_admin_only")
    changes = {}
    if full_name is not None:
        name = registration.clean_name(full_name)
        if name != target.full_name:
            changes["full_name"] = [target.full_name, name]
            target.full_name = name
    if phone is not None:
        number = registration.clean_phone(phone)
        if number != target.phone:
            if await session.scalar(select(User.id).where(User.phone == number, User.id != target.id)):
                raise AppError("phone_taken", "This phone number is already used by another account. Please contact an administrator.", 409)
            changes["phone"] = [target.phone, number]
            target.phone = number
            target.phone_verified = False  # typed by hand, not shared through Telegram
    if changes:
        audit.record(session, actor.id, "user.edited", target_user_id=target.id, entity="user", entity_id=target.id, changes=changes)
        await session.commit()


async def unlink(session: AsyncSession, settings: Settings, actor: User, target: User, keep_recordings: bool = True) -> str:
    """Remove the UseMinute email from a user (LNK-15). With `keep_recordings` the recordings already counted
    stay in their earnings (LNK-14, a replaced account); without, they leave (linked to the wrong person).
    Returns the email. Commits."""
    require_admin(actor, settings)
    _guard_target(actor, target, settings)
    link = await session.scalar(select(DoppiLink).where(DoppiLink.user_id == target.id, DoppiLink.unlinked_at.is_(None)))
    if link is None:
        raise AppError("not_linked", "This person has no UseMinute email linked", 409)
    link.unlinked_at = clock.now()
    link.unlinked_by_id = actor.id
    link.keep_recordings = keep_recordings
    audit.record(session, actor.id, "link.removed", target_user_id=target.id, entity="link", entity_id=link.id,
                 email=link.email, keep_recordings=keep_recordings)
    await session.commit()
    return link.email
