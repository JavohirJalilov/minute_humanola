"""What every API request starts with: a database session, the caller, and their role.

Roles are checked here and again inside the services (ROLE-5): hiding a button in the Web App is never
what protects data.
"""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from backend import db, roles
from backend.config import Settings, get_settings
from backend.errors import AppError, forbidden
from backend.models import User
from backend.security import InitDataError, same_key, validate_init_data
from backend.services import registration, transfers

SessionDep = Annotated[AsyncSession, Depends(db.get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def current_user(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_debug_user_id: Annotated[int | None, Header()] = None,
) -> User:
    """The registered, unblocked user behind a Web App request."""
    if settings.debug_auth_enabled and x_debug_user_id is not None:
        telegram_id = x_debug_user_id  # developer machine only (NFR-6)
    else:
        scheme, _, init_data = (authorization or "").partition(" ")
        if scheme.lower() != "tma":
            raise AppError("not_telegram", "Open this app from the Telegram bot", 401)
        try:
            telegram_id = validate_init_data(init_data, settings.bot_token, settings.init_data_max_age).id
        except InitDataError as e:
            raise AppError("bad_init_data", str(e), 401)
    user = await registration.find_user(session, telegram_id)
    if user is None:
        # ONB-11: opened the Web App link without registering
        raise AppError("not_registered", "You are not registered yet. Open the bot, press Start and register first.", 403)
    if user.is_blocked:
        # ROLE-6: a blocked user cannot open the Web App
        raise AppError("blocked", "Your access has been disabled. Contact an administrator.", 403)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def staff_user(user: CurrentUser, settings: SettingsDep) -> User:
    if not roles.is_staff(user, settings):
        raise forbidden("Staff only", "staff_only")
    return user


async def admin_user(user: CurrentUser, settings: SettingsDep) -> User:
    if not roles.is_admin(user, settings):
        raise forbidden("Admins only", "admins_only")
    return user


async def super_admin_user(user: CurrentUser, settings: SettingsDep) -> User:
    if not roles.is_super_admin(user, settings):
        raise forbidden("Super Admin only", "super_admin_only")
    return user


StaffUser = Annotated[User, Depends(staff_user)]
AdminUser = Annotated[User, Depends(admin_user)]
SuperAdminUser = Annotated[User, Depends(super_admin_user)]


async def visible_user(session: AsyncSession, settings: Settings, viewer: User, user_id: int | None) -> User:
    """The person whose phone and money a request is about. NFR-2: a worker gets only their own data, a
    staff member only their own workers', an Admin anyone's."""
    if user_id is None or user_id == viewer.id:
        return viewer
    target = await session.get(User, user_id)
    if target is None:
        raise AppError("not_found", "This person was not found", 404)
    if roles.is_admin(viewer, settings):
        return target
    if roles.is_staff(viewer, settings) and await transfers.current_staff_id(session, target.id) == viewer.id:
        return target
    raise forbidden("You can only see workers assigned to you", "not_your_worker")


async def bot_only(settings: SettingsDep, x_bot_key: Annotated[str | None, Header()] = None) -> None:
    """The /internal API is for the bot alone."""
    if not same_key(x_bot_key, settings.bot_api_key):
        raise AppError("bot_only", "Not allowed", 401)
