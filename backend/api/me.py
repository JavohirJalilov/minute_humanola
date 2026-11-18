"""Who am I, linking a UseMinute account, and My Progress (sections 5 and 7)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy import select

from backend import jobs, roles
from backend.deps import CurrentUser, SessionDep, SettingsDep, visible_user
from backend.errors import AppError
from backend.models import AppState, Recording, User
from backend.services import amounts, doppi, linking, overview, registration

router = APIRouter(prefix="/api", tags=["me"])


class LanguageIn(BaseModel):
    language: str


class InviteIn(BaseModel):
    code: str = ""


class EmailIn(BaseModel):
    email: str = ""


class CodeIn(BaseModel):
    code: str = ""


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    """Who the user is and which sections they have (section 5)."""
    role = roles.role_of(user, settings)
    staff = roles.is_staff(user, settings)
    admin = roles.is_admin(user, settings)
    bot = await session.get(AppState, "bot")
    return {
        "user": {"id": user.id, "name": user.full_name, "phone": user.phone, "role": role, "language": user.language},
        "linked": await linking.current_link(session, user.id) is not None,
        # APP-2: Users is for Admins; the server refuses its data to everyone else as well
        "sections": ["progress", "payments"] + (["users", "useminute"] if admin else []),
        "payments": ["money"] + (["workers", "funds"] if staff else []) + (["staff"] if admin else []),
        # The first screen is My Progress for everyone
        "start": "progress",
        "timezone": settings.timezone,
        "today": settings.today().isoformat(),
        "bot": bot.value if bot else {},
        "support": await support_link(session, settings),
    }


async def support_link(session, settings) -> str | None:
    """Where "Contact us" leads: SUPPORT_CONTACT, else a Super Admin's or an Admin's Telegram chat."""
    contact = settings.support_contact.strip()
    if contact.startswith(("https://", "http://", "tg://")):
        return contact
    if contact:
        return "https://t.me/" + contact.lstrip("@")
    people = list(await session.scalars(select(User).where(User.username.is_not(None), User.is_blocked.is_(False)).order_by(User.id)))
    for wanted in (lambda u: roles.is_super_admin(u, settings), lambda u: roles.is_admin(u, settings)):
        for u in people:
            if wanted(u):
                return "https://t.me/" + u.username
    return None


@router.post("/me/language")
async def set_language(body: LanguageIn, user: CurrentUser, session: SessionDep) -> dict:
    """APP-8: the choice is remembered per user and also applies to bot messages."""
    if body.language not in registration.LANGUAGES:
        raise AppError("language_invalid", "Choose Uzbek, Russian or English")
    user.language = body.language
    await session.commit()
    return {"language": user.language}


# ---- linking (section 7.1)


@router.get("/link")
async def link_state(user: CurrentUser, session: SessionDep) -> dict:
    return await linking.state(session, user)


@router.post("/link/invite")
async def link_invite(body: InviteIn, user: CurrentUser, session: SessionDep) -> dict:
    await linking.submit_invite_code(session, user, body.code)
    return await _state(session, user)


@router.post("/link/email")
async def link_email(body: EmailIn, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    await linking.request_code(session, settings, user, body.email)
    return await _state(session, user)


@router.post("/link/resend")
async def link_resend(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    await linking.request_code(session, settings, user)
    return await _state(session, user)


@router.post("/link/change-email")
async def link_change_email(user: CurrentUser, session: SessionDep) -> dict:
    await linking.change_email(session, user)
    return await _state(session, user)


@router.post("/link/verify")
async def link_verify(body: CodeIn, user: CurrentUser, session: SessionDep, settings: SettingsDep, background: BackgroundTasks) -> dict:
    linked = await linking.verify(session, settings, user, body.code)
    background.add_task(jobs.after_link, settings, user.id, linked.link.email)
    return await _state(session, user)


async def _state(session, user) -> dict:
    state = await linking.state(session, user)
    await session.commit()  # release the database before the response goes out
    return state


# ---- My Progress (section 7.2)


@router.get("/progress")
async def progress(
    user: CurrentUser, session: SessionDep, settings: SettingsDep,
    start: date | None = None, end: date | None = None, user_id: int | None = None,
) -> dict:
    """The phone with its accepted, pending and rejected time and money, for a period of recording days."""
    target = await visible_user(session, settings, user, user_id)
    return await overview.progress(session, settings, target, start, end)


@router.get("/recordings/days")
async def recording_days(
    user: CurrentUser, session: SessionDep, settings: SettingsDep,
    email: str | None = None, start: date | None = None, end: date | None = None, user_id: int | None = None,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    """A phone's recording days, newest first, each split into accepted, pending and rejected."""
    target = await visible_user(session, settings, user, user_id)
    return await overview.recording_days(session, settings, target, email=email, start=start, end=end, limit=limit, offset=offset)


@router.get("/recordings")
async def recordings(
    user: CurrentUser, session: SessionDep, settings: SettingsDep,
    email: str | None = None, start: date | None = None, end: date | None = None, status: str | None = None,
    user_id: int | None = None, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """A phone's recordings, newest first, with UseMinute's reason for each rejection (PRG-6)."""
    target = await visible_user(session, settings, user, user_id)
    return await overview.recordings(session, settings, target, email=email, start=start, end=end, status=status,
                                     limit=limit, offset=offset)


@router.get("/recordings/{recording_id}/video")
async def recording_video(recording_id: str, user: CurrentUser, session: SessionDep, settings: SettingsDep,
                          user_id: int | None = None) -> dict:
    """Fresh links to a recording's videos, flagged ones first with the reviewer's reason. The same people
    who see the recording may open it: the worker, their staff member and Admins."""
    target = await visible_user(session, settings, user, user_id)
    recording = await session.get(Recording, recording_id)
    owned = [o.email for o in (await amounts.owned_emails(session, [target.id])).get(target.id, [])]
    if recording is None or recording.email not in owned:
        raise AppError("not_found", "This recording was not found", 404)
    await session.commit()  # nothing waits on the database while UseMinute answers
    try:
        files = await doppi.video(settings, recording)
    except doppi.DoppiError as e:
        raise AppError("video_unavailable", str(e), 502)
    except Exception:
        raise AppError("video_unavailable", "UseMinute didn't answer. Please try again in a minute.", 502)
    if not files:
        raise AppError("video_missing", "UseMinute has no video for this recording", 404)
    return {"files": files}
