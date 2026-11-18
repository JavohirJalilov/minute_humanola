"""Linking a UseMinute account (section 7.1): invite code, then email, then a code sent to that email.

Limits (LNK-2, LNK-6..8): 5 wrong invite codes lock the form for 15 minutes; a verification code is
6 digits, valid 10 minutes, usable once, cancelled after 5 wrong entries; a new code can be requested
60 seconds after the last one, at most 5 per hour.
"""

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, roles
from backend.config import Settings
from backend.errors import AppError
from backend.models import (
    REC_REJECTED,
    AppState,
    DoppiAccount,
    DoppiLink,
    InviteCode,
    LinkProgress,
    Phone,
    Recording,
    StaffAssignment,
    User,
    VerificationAttempt,
)
from backend.services import audit, mailer, notify

INVITE_FORMAT = re.compile(r"^[A-Z0-9]{8}$")
MAX_WRONG_INVITES = 5
INVITE_LOCK = timedelta(minutes=15)
CODE_TTL = timedelta(minutes=10)
RESEND_AFTER = timedelta(seconds=60)
CODES_PER_HOUR = 5
MAX_WRONG_CODES = 5
EMAIL_FORMAT = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

TAKEN = "This email is already linked to another user. Please contact an administrator."


def normalize_invite(raw: str | None) -> str:
    """Not case-sensitive; surrounding spaces are ignored (LNK-1)."""
    return (raw or "").strip().upper()


def normalize_email(raw: str | None) -> str:
    """Trimmed, lower case, and a valid format (LNK-4)."""
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_FORMAT.match(email):
        raise AppError("email_invalid", "This doesn't look like an email address")
    return email


def hash_code(settings: Settings, user_id: int, email: str, code: str) -> str:
    return hmac.new(settings.derive("verification").encode(), f"{user_id}:{email}:{code}".encode(), hashlib.sha256).hexdigest()


async def current_link(session: AsyncSession, user_id: int) -> DoppiLink | None:
    return await session.scalar(select(DoppiLink).where(DoppiLink.user_id == user_id, DoppiLink.unlinked_at.is_(None)))


async def _progress(session: AsyncSession, user_id: int) -> LinkProgress:
    p = await session.get(LinkProgress, user_id)
    if p is None:
        p = LinkProgress(user_id=user_id, wrong_codes=0)
        session.add(p)
        await session.flush()
    return p


async def _must_be_unlinked(session: AsyncSession, user: User) -> None:
    if await current_link(session, user.id):
        # LNK-13: one worker, one UseMinute email
        raise AppError("already_linked", "Your UseMinute account is already linked", 409)


async def _send_times(session: AsyncSession, user_id: int) -> list:
    q = select(VerificationAttempt.sent_at).where(
        VerificationAttempt.user_id == user_id, VerificationAttempt.sent_at > clock.now() - timedelta(hours=1)
    )
    return sorted(await session.scalars(q))


async def _active_attempt(session: AsyncSession, user_id: int, email: str) -> VerificationAttempt | None:
    q = (
        select(VerificationAttempt)
        .where(VerificationAttempt.user_id == user_id, VerificationAttempt.email == email,
               VerificationAttempt.used_at.is_(None), VerificationAttempt.cancelled_at.is_(None))
        .order_by(VerificationAttempt.sent_at.desc(), VerificationAttempt.id.desc())
        .limit(1)
    )
    return await session.scalar(q)


async def state(session: AsyncSession, user: User) -> dict:
    """What the linking form shows right now."""
    link = await current_link(session, user.id)
    if link is not None:
        return {"step": "linked", "email": link.email, "linked_at": link.verified_at.isoformat()}
    now = clock.now()
    p = await session.get(LinkProgress, user.id)
    if p is None or p.invite_code_id is None:
        locked = p.locked_until if p and p.locked_until and p.locked_until > now else None
        return {"step": "invite", "locked_until": locked.isoformat() if locked else None,
                "attempts_left": MAX_WRONG_INVITES - (p.wrong_codes if p else 0)}
    invite = await session.get(InviteCode, p.invite_code_id)
    out = {"step": "email", "invite_code": invite.code if invite else None}
    if p.pending_email:
        sends = await _send_times(session, user.id)
        attempt = await _active_attempt(session, user.id, p.pending_email)
        resend_at = sends[-1] + RESEND_AFTER if sends else now
        if len(sends) >= CODES_PER_HOUR:
            resend_at = max(resend_at, sends[0] + timedelta(hours=1))
        out.update(
            step="code",
            email=p.pending_email,
            # False: expired or cancelled after 5 wrong entries, so a new code must be requested (LNK-8)
            code_active=bool(attempt and attempt.expires_at > now),
            expires_at=attempt.expires_at.isoformat() if attempt else None,
            resend_at=resend_at.isoformat(),
        )
    return out


async def submit_invite_code(session: AsyncSession, user: User, raw: str | None) -> None:
    """Step 1. A wrong or deactivated code keeps the worker here (LNK-2). Commits."""
    await _must_be_unlinked(session, user)
    p = await _progress(session, user.id)
    now = clock.now()
    if p.locked_until and p.locked_until > now:
        minutes = math.ceil((p.locked_until - now).total_seconds() / 60)
        raise AppError("invite_locked", "Too many wrong codes. Try again in {minutes} min.", 429,
                       minutes=minutes, locked_until=p.locked_until.isoformat())
    text = normalize_invite(raw)
    invite = await session.scalar(select(InviteCode).where(InviteCode.code == text)) if INVITE_FORMAT.match(text) else None
    if invite is None or not invite.is_active:
        p.wrong_codes += 1
        if p.wrong_codes >= MAX_WRONG_INVITES:
            p.wrong_codes = 0
            p.locked_until = now + INVITE_LOCK
            await session.commit()
            raise AppError("invite_locked", "Too many wrong codes. Try again in {minutes} min.", 429,
                           minutes=int(INVITE_LOCK.total_seconds() // 60), locked_until=p.locked_until.isoformat())
        left = MAX_WRONG_INVITES - p.wrong_codes
        await session.commit()
        raise AppError("invite_wrong", "This invite code is not valid. Check it and try again.", attempts_left=left)
    p.invite_code_id = invite.id
    p.invite_code_at = now  # LNK-3: which code, and when
    p.wrong_codes = 0
    p.locked_until = None
    p.pending_email = None
    await session.commit()


async def _accepted_invite(session: AsyncSession, p: LinkProgress) -> InviteCode:
    """The invite code accepted in step 1, if it is still active (SET-4)."""
    if p.invite_code_id is None:
        raise AppError("invite_required", "Enter your invite code first", 409)
    invite = await session.get(InviteCode, p.invite_code_id)
    if invite is None or not invite.is_active:
        p.invite_code_id = p.invite_code_at = p.pending_email = None
        await session.commit()
        raise AppError("invite_inactive", "This invite code is no longer active. Enter a valid one.", 409)
    return invite


async def request_code(session: AsyncSession, settings: Settings, user: User, raw_email: str | None = None) -> None:
    """Steps 2 and "Resend code": email a fresh verification code. `raw_email` empty resends to the
    email already entered. Commits.

    The code is saved first and the transaction closed before the email goes out, so the database is
    never kept waiting for the mail server. If sending fails the code is removed again, which also
    means the failed attempt doesn't count against the hourly limit (EXT-15)."""
    await _must_be_unlinked(session, user)
    p = await _progress(session, user.id)
    await _accepted_invite(session, p)
    email = normalize_email(raw_email) if raw_email is not None else p.pending_email
    if not email:
        raise AppError("email_required", "Enter the email of your UseMinute account", 409)
    if await session.scalar(select(DoppiLink.id).where(DoppiLink.email == email, DoppiLink.unlinked_at.is_(None))):
        raise AppError("email_taken", TAKEN, 409)  # LNK-5
    await _must_be_account(session, email)

    now = clock.now()
    sends = await _send_times(session, user.id)
    if sends and now - sends[-1] < RESEND_AFTER:
        wait = sends[-1] + RESEND_AFTER
        raise AppError("resend_wait", "You can ask for a new code in {seconds} s.", 429,
                       seconds=math.ceil((wait - now).total_seconds()), resend_at=wait.isoformat())
    if len(sends) >= CODES_PER_HOUR:
        wait = sends[0] + timedelta(hours=1)
        raise AppError("too_many_codes", "Too many codes were requested. Try again in {minutes} min.", 429,
                       minutes=math.ceil((wait - now).total_seconds() / 60), resend_at=wait.isoformat())

    code = f"{secrets.randbelow(10**6):06d}"
    previous = [a.id for a in await session.scalars(select(VerificationAttempt).where(
        VerificationAttempt.user_id == user.id, VerificationAttempt.used_at.is_(None), VerificationAttempt.cancelled_at.is_(None)))]
    if previous:
        await session.execute(update(VerificationAttempt).where(VerificationAttempt.id.in_(previous)).values(cancelled_at=now))
    attempt = VerificationAttempt(user_id=user.id, email=email, code_hash=hash_code(settings, user.id, email, code),
                                  sent_at=now, expires_at=now + CODE_TTL, wrong_count=0)
    session.add(attempt)
    before = p.pending_email
    p.pending_email = email
    bot = (await session.get(AppState, "bot")) or AppState(value={})
    bot_name, bot_username, language = bot.value.get("name"), bot.value.get("username"), user.language
    await session.commit()

    try:
        await mailer.send_verification(settings, email, code, language, bot_name, bot_username)
    except mailer.MailError:
        await session.execute(delete(VerificationAttempt).where(VerificationAttempt.id == attempt.id))
        await session.execute(update(LinkProgress).where(LinkProgress.user_id == user.id).values(pending_email=before))
        await session.commit()
        raise AppError("email_failed", "The email could not be sent. Please try again.", 502)


async def _must_be_account(session: AsyncSession, email: str) -> None:
    """The email must be an active account on UseMinute, so a typo is caught before a code goes out. Until
    the first sync has brought the account list there is nothing to check against."""
    if not await session.scalar(select(DoppiAccount.email).limit(1)):
        return
    account = await session.get(DoppiAccount, email)
    if account is None:
        raise AppError("email_unknown", "There is no UseMinute account with this email. Use the email you sign in to the UseMinute app with.")
    if not account.active:
        raise AppError("account_off", "This UseMinute account is switched off. Please contact us.", 409)


async def change_email(session: AsyncSession, user: User) -> None:
    """Back from the code step to correct a mistyped email (LNK-10). Commits."""
    p = await _progress(session, user.id)
    p.pending_email = None
    await session.execute(
        update(VerificationAttempt)
        .where(VerificationAttempt.user_id == user.id, VerificationAttempt.used_at.is_(None), VerificationAttempt.cancelled_at.is_(None))
        .values(cancelled_at=clock.now())
    )
    await session.commit()


@dataclass
class Linked:
    link: DoppiLink
    staff_id: int | None  # the staff member the invite code assigned the worker to, if it did


async def verify(session: AsyncSession, settings: Settings, user: User, raw_code: str | None) -> Linked:
    """Step 3: the code proves the worker reads that mailbox, and the email becomes their phone. Commits."""
    await _must_be_unlinked(session, user)
    p = await _progress(session, user.id)
    invite = await _accepted_invite(session, p)
    email = p.pending_email
    attempt = await _active_attempt(session, user.id, email) if email else None
    now = clock.now()
    if attempt is None:
        raise AppError("code_cancelled", "This code no longer works. Ask for a new one.", 410)
    if attempt.expires_at <= now:
        raise AppError("code_expired", "This code has expired. Ask for a new one.", 410)
    code = "".join(ch for ch in (raw_code or "") if ch.isdigit())
    if not hmac.compare_digest(hash_code(settings, user.id, email, code), attempt.code_hash):
        attempt.wrong_count += 1
        if attempt.wrong_count >= MAX_WRONG_CODES:
            attempt.cancelled_at = now  # LNK-8
            await session.commit()
            raise AppError("code_cancelled", "Too many wrong entries. Ask for a new code.", 410)
        left = MAX_WRONG_CODES - attempt.wrong_count
        await session.commit()
        raise AppError("code_wrong", "Wrong code. Check the email and try again.", attempts_left=left)

    if await session.scalar(select(DoppiLink.id).where(DoppiLink.email == email, DoppiLink.unlinked_at.is_(None))):
        raise AppError("email_taken", TAKEN, 409)
    attempt.used_at = now
    link = DoppiLink(user_id=user.id, email=email, invite_code_id=invite.id, verified_at=now, keep_recordings=True)
    session.add(link)
    try:
        await session.flush()
    except IntegrityError:  # the unique index is the last word on "one email, one user"
        await session.rollback()
        raise AppError("email_taken", TAKEN, 409)

    # The phone exists from now on, even before UseMinute has recordings for it (LNK-12)
    if await session.get(Phone, email) is None:
        session.add(Phone(email=email, recordings=0))
    # Rejections from before the link are in the app; the chat gets only new ones
    await session.execute(
        update(Recording)
        .where(Recording.email == email, Recording.status == REC_REJECTED, Recording.rejection_notified_at.is_(None))
        .values(rejection_notified_at=now)
    )
    staff = await _assign_from_invite(session, settings, user, invite)
    await session.delete(p)
    audit.record(session, user.id, "link.created", target_user_id=user.id, entity="link", entity_id=link.id,
                 email=email, invite_code=invite.code)
    if staff is not None:
        notify.assigned(session, user, staff)
    await session.commit()
    return Linked(link, staff.id if staff else None)


async def _assign_from_invite(session: AsyncSession, settings: Settings, user: User, invite: InviteCode) -> User | None:
    """USR-11, SET-6: a code that belongs to a staff member assigns the worker to them. An assignment an
    Admin already made is left alone, and nobody becomes their own staff member (USR-15)."""
    if not invite.staff_id or invite.staff_id == user.id:
        return None
    has_staff = await session.scalar(
        select(StaffAssignment.id).where(StaffAssignment.worker_id == user.id, StaffAssignment.ended_at.is_(None))
    )
    staff = await session.get(User, invite.staff_id)
    if has_staff or staff is None or staff.is_blocked or not roles.is_staff(staff, settings):
        return None
    session.add(StaffAssignment(worker_id=user.id, staff_id=staff.id, started_at=clock.now(), set_by_id=None, source="invite_code"))
    audit.record(session, None, "staff.assigned", target_user_id=user.id, entity="user", entity_id=user.id,
                 staff_id=staff.id, source="invite_code", invite_code=invite.code)
    return staff


async def recordings_found(session: AsyncSession, email: str) -> int:
    return int(await session.scalar(select(func.count()).select_from(Recording).where(Recording.email == email)) or 0)
