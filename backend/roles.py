"""The four roles (section 3). Worker, Staff and Admin are stored on the user; Super Admin comes only from
the server configuration (SUPER_ADMIN_IDS) and outranks whatever is stored."""

from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings
from backend.models import User


class Role(StrEnum):
    WORKER = "worker"
    STAFF = "staff"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


RANK = {Role.WORKER: 0, Role.STAFF: 1, Role.ADMIN: 2, Role.SUPER_ADMIN: 3}
STORED_ROLES = (Role.WORKER, Role.STAFF, Role.ADMIN)


def role_of(user: User, settings: Settings) -> Role:
    if settings.is_super_admin(user.telegram_id):
        return Role.SUPER_ADMIN
    return Role(user.role)


def rank_of(user: User, settings: Settings) -> int:
    return RANK[role_of(user, settings)]


def is_staff(user: User, settings: Settings) -> bool:
    """Staff or higher: can be responsible for workers, pay them and receive funding."""
    return rank_of(user, settings) >= RANK[Role.STAFF]


def is_admin(user: User, settings: Settings) -> bool:
    return rank_of(user, settings) >= RANK[Role.ADMIN]


def is_super_admin(user: User, settings: Settings) -> bool:
    return settings.is_super_admin(user.telegram_id)


def staff_condition(settings: Settings):
    """SQL condition: users who are Staff or higher."""
    return or_(User.role.in_((Role.STAFF, Role.ADMIN)), User.telegram_id.in_(settings.super_admin_ids or [0]))


def admin_condition(settings: Settings):
    """SQL condition: users who are Admin or Super Admin."""
    return or_(User.role == Role.ADMIN, User.telegram_id.in_(settings.super_admin_ids or [0]))


async def admins(session: AsyncSession, settings: Settings) -> list[User]:
    """Admins and Super Admins who can be told about things."""
    return list(await session.scalars(select(User).where(admin_condition(settings), User.is_blocked.is_(False))))


async def super_admins(session: AsyncSession, settings: Settings) -> list[User]:
    if not settings.super_admin_ids:
        return []
    return list(await session.scalars(select(User).where(User.telegram_id.in_(settings.super_admin_ids))))


async def staff_options(session: AsyncSession, settings: Settings) -> list[User]:
    """People who can be made responsible for workers: Staff and higher, not blocked."""
    q = select(User).where(staff_condition(settings), User.is_blocked.is_(False)).order_by(func.lower(User.full_name))
    return list(await session.scalars(q))
