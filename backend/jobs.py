"""Work the backend does on its own: the scheduled UseMinute sync, reminders about unanswered transfers,
the daily backup, and housekeeping. Each loop is safe to run in several backend processes at once."""

import asyncio
import logging
from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from backend import backup, clock, db
from backend.config import Settings
from backend.models import AWAITING, AppState, Transfer, User
from backend.services import doppi, linking, notify, outbox

log = logging.getLogger(__name__)

REMIND_AFTER = timedelta(hours=24)
MAX_REMINDERS = 7


async def sync_and_notify(settings: Settings, days: int | None = None) -> doppi.Stats:
    """A UseMinute sync with what follows it: workers hear about new rejections (once a day at most), and
    the Super Admins hear about a failure (EXT-11)."""
    try:
        stats = await doppi.run(settings, days)
    except doppi.SyncBusy:
        raise
    except doppi.DoppiError as e:
        async with db.WriteSession() as session:
            await notify.sync_failed(session, settings, str(e))
            await session.commit()
        outbox.wake()
        raise
    async with db.WriteSession() as session:
        await notify.rejections(session, settings)
        await session.commit()
    outbox.wake()
    return stats


async def start_sync(settings: Settings) -> None:
    """Sync now, in the background (SET-13). Failures are already recorded for Settings."""
    try:
        await sync_and_notify(settings)
    except doppi.DoppiError:
        pass
    except Exception:
        log.exception("Sync failed")


async def after_link(settings: Settings, user_id: int, email: str) -> None:
    """LNK-11: right after linking, fetch from UseMinute so the worker doesn't wait for the scheduled sync;
    then tell them the email is linked and how many recordings were found."""
    try:
        async with db.ReadSession() as session:
            fresh = await doppi.is_fresh(session)
        if settings.doppi_configured and not fresh:
            await sync_and_notify(settings)
    except doppi.DoppiError:
        pass  # the stored copy is shown, with its timestamp (EXT-6)
    except Exception:
        log.exception("Sync after linking failed")
    async with db.WriteSession() as session:
        user = await session.get(User, user_id)
        if user is not None:
            notify.linked(session, settings, user, email, await linking.recordings_found(session, email))
            await session.commit()
    outbox.wake()


async def send_reminders(settings: Settings) -> int:
    """PAY-7: nothing is accepted automatically. A transfer nobody answered is brought up again after
    24 hours, then once a day, for up to 7 days."""
    now = clock.now()
    async with db.WriteSession() as session:
        q = (
            select(Transfer)
            .where(Transfer.state == AWAITING, Transfer.reminders_sent < MAX_REMINDERS,
                   func.coalesce(Transfer.reminded_at, Transfer.created_at) <= now - REMIND_AFTER)
            .order_by(Transfer.created_at)
            .limit(200)
        )
        sent = 0
        for transfer in await session.scalars(q):
            transfer.reminders_sent += 1
            transfer.reminded_at = now
            receiver = await session.get(User, transfer.receiver_id)
            if receiver is None or receiver.is_blocked:
                continue  # PAY-11: a blocked user can't answer
            await notify.transfer_created(session, settings, transfer, reminder=True)
            sent += 1
        await session.commit()
    if sent:
        outbox.wake()
    return sent


async def backup_if_due(settings: Settings) -> bool:
    """One backup a day, at BACKUP_TIME."""
    if not settings.backup_time:
        return False
    now = clock.now().astimezone(settings.tz)
    if now < datetime.combine(now.date(), time.fromisoformat(settings.backup_time), settings.tz):
        return False
    today = now.date().isoformat()
    async with db.WriteSession() as session:
        state = await session.get(AppState, "backup")
        if state is not None and state.value.get("day") == today:
            return False
        if state is None:
            session.add(AppState(key="backup", value={"day": today}))
        else:
            state.value = {"day": today}
        await session.commit()  # claimed: no other process makes today's backup
    path = await backup.run(settings)
    log.info("Database backup written: %s", path.name)
    return True


async def _loop(name: str, every: float, step) -> None:
    while True:
        try:
            await step()
        except asyncio.CancelledError:
            raise
        except doppi.DoppiError:
            pass  # recorded in sync_state and reported to the Super Admins
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(every)


def start(settings: Settings) -> list[asyncio.Task]:
    async def sync_step():
        if await doppi.due(settings):
            await sync_and_notify(settings)

    async def tidy_step():
        async with db.WriteSession() as session:
            await outbox.tidy(session)

    return [
        asyncio.create_task(_loop("UseMinute sync", 60, sync_step)),
        asyncio.create_task(_loop("Reminders", 300, lambda: send_reminders(settings))),
        asyncio.create_task(_loop("Backup", 300, lambda: backup_if_due(settings))),
        asyncio.create_task(_loop("Outbox tidy", 6 * 3600, tidy_step)),
    ]
