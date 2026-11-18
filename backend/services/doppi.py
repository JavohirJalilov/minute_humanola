"""Recording data from UseMinute (section 13.2).

UseMinute gives the company no API key, so the system signs in the way the partner portal does (a web key,
a refresh token and the organisation id, kept in the server configuration) and reads the organisation's
upload report. It only ever reads (EXT-3).

A sync re-reads the last DOPPI_SYNC_DAYS days, adds new recordings and updates the review status of known
ones (EXT-4), keeps recordings of emails nobody has linked yet (EXT-12), and then looks up the reviewer's
reason for each rejected recording. Web App screens read only the stored copy (EXT-6).

One sync runs at a time, across every server process: the sync_state row holds the lock (SET-13).
Nothing is fetched while a database transaction is open.
"""

import asyncio
import hashlib
import logging
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, db
from backend.config import Settings
from backend.models import REC_ACCEPTED, REC_PENDING, REC_REJECTED, DoppiAccount, DoppiLink, Phone, Recording, SyncState, User

log = logging.getLogger(__name__)

# UseMinute's review status → the three parts users see. "flagged" is UseMinute's word for rejected;
# anything unknown is treated as pending (EXT-10)
STATUS = {"accepted": REC_ACCEPTED, "pending": REC_PENDING, "flagged": REC_REJECTED, "rejected": REC_REJECTED}

# EXT-9: some iPhones report the recording time 12 hours ahead. A recording can't end after it was
# uploaded, so when recorded + length is later than the upload (with a little slack for clock drift)
# the time is moved back 12 hours.
CLOCK_AHEAD = timedelta(hours=12)
CLOCK_SLACK = timedelta(minutes=3)

MS_PER_MINUTE = Decimal(60_000)
LOCK_STALE = timedelta(minutes=10)  # a sync that hasn't shown it is alive for this long has died
REASON_WORKERS = 6
REASONS_PER_SYNC = 3000
ACCOUNTS_PAGE = 50  # UseMinute refuses bigger pages of its users list
RETRY_AFTER = timedelta(hours=1)
FRESH = timedelta(minutes=5)
STORE_CHUNK = 300
UPDATED = ("user_key", "email", "phone_model", "device_type", "recorded_at", "uploaded_at", "day", "minutes",
           "duration_ms", "review_status", "status", "verdict", "flagged_videos", "chunks", "task_name", "category",
           "rate_multiplier", "clock_fixed", "synced_at")


class DoppiError(Exception):
    """A sync problem, with a message an Admin can read in Settings."""


class LoginRefused(DoppiError):
    """UseMinute refused the login: syncing stops until the configuration changes (EXT-11)."""


class SyncBusy(DoppiError):
    pass


@dataclass
class Stats:
    recordings: int = 0
    new: int = 0
    phones: int = 0
    clock_fixed: int = 0
    reasons: int = 0
    days: int = 0
    accounts: int = 0  # active accounts on UseMinute


def fix_clock(recorded: datetime, uploaded: datetime | None, minutes: Decimal) -> tuple[datetime, bool]:
    """The real recording time, and whether it had to be moved back."""
    if uploaded and recorded + timedelta(minutes=float(minutes)) > uploaded + CLOCK_SLACK:
        return recorded - CLOCK_AHEAD, True
    return recorded, False


def _time(value) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _cut(value, size: int) -> str | None:
    value = str(value).strip() if value not in (None, "") else ""
    return value[:size] or None


def normalize(row: dict, tz: ZoneInfo) -> dict | None:
    """One row of UseMinute's upload report as a Recording. None for a row that can't be used."""
    recording_id = _cut(row.get("sessionId"), 64)
    email = (row.get("email") or "").strip().lower()  # EXT-7: matched in lower case
    try:
        recorded = _time(row.get("recordedAt"))
        uploaded = _time(row.get("uploadedAt"))
        minutes = Decimal(str(row.get("minutesCollected") or 0))
        multiplier = float(row["rateMultiplier"]) if row.get("rateMultiplier") not in (None, "") else None
    except (ValueError, InvalidOperation, TypeError):
        return None
    if not recording_id or not email or len(email) > 254 or recorded is None or not minutes.is_finite() or minutes < 0:
        return None
    recorded, fixed = fix_clock(recorded, uploaded, minutes)
    review = (row.get("reviewStatus") or "pending").strip().lower()[:32]
    flag = row.get("flag") or {}
    return {
        "id": recording_id,
        "user_key": _cut(row.get("userResourceKey"), 64),
        "email": email,
        "phone_model": _cut(row.get("phoneModel"), 100),
        "device_type": _cut(row.get("deviceType"), 50),
        "recorded_at": recorded,
        "uploaded_at": uploaded,
        "day": recorded.astimezone(tz).date(),  # EXT-8: the Tashkent calendar day
        "minutes": float(minutes),
        # The length every amount is calculated from: whole milliseconds, so sums are exact
        "duration_ms": int((minutes * MS_PER_MINUTE).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        "review_status": review,
        "status": STATUS.get(review, REC_PENDING),
        "verdict": _cut(flag.get("verdict"), 32),
        "flagged_videos": _int(flag.get("flaggedVideoCount")),
        "chunks": _int(row.get("chunkCount")),
        "task_name": _cut(row.get("taskName"), 255),
        "category": _cut(row.get("taskCategory"), 255),
        "rate_multiplier": multiplier,
        "clock_fixed": fixed,
    }


# ---- talking to UseMinute

_refresh_token: str | None = None  # the sign-in may hand out a newer refresh token; keep using the newest


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60)


def _org_url(settings: Settings) -> str:
    return f"{settings.doppi_api_url.rstrip('/')}/organizations/{settings.doppi_org}"


async def _token(http: httpx.AsyncClient, settings: Settings) -> str:
    global _refresh_token
    refresh = _refresh_token or settings.doppi_refresh_token
    r = await http.post(
        settings.doppi_token_url,
        params={"key": settings.doppi_api_key},
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Referer": settings.doppi_referer},
    )
    if r.status_code in (400, 401, 403):
        raise LoginRefused("The UseMinute login has expired: put a new DOPPI_REFRESH_TOKEN in the server configuration and restart")
    r.raise_for_status()
    j = r.json()
    _refresh_token = j.get("refresh_token") or refresh
    return j["id_token"]


async def _get(http: httpx.AsyncClient, url: str, params: dict, token: str) -> dict:
    for attempt in range(5):
        try:
            r = await http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.TransportError:
            if attempt == 4:
                raise
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code in (401, 403):
            raise LoginRefused(f"UseMinute refused the request ({r.status_code}): check DOPPI_ORG and the login")
        r.raise_for_status()
        return r.json()
    raise DoppiError("UseMinute did not answer")


async def fetch_report(settings: Settings, days: int, beat=None) -> list[dict]:
    """Every upload of the last `days` days (by upload time), one row per recording."""
    end = clock.now().date() + timedelta(days=1)
    start = end - timedelta(days=days + 1)
    rows, seen, cursor = [], set(), None
    async with _http() as http:
        token = await _token(http, settings)
        url = _org_url(settings) + "/uploads/report"
        while True:
            params = {"tz": "UTC", "start_date": f"{start}T00:00:00.000Z", "end_date": f"{end}T00:00:00.000Z",
                      "limit": "200", "include_flags": "true"}
            if cursor:
                params["cursor"] = cursor
            j = await _get(http, url, params, token)
            for r in j.get("rows") or []:
                if r.get("sessionId") and r["sessionId"] not in seen:
                    seen.add(r["sessionId"])
                    rows.append(r)
            if beat is not None:
                await beat()
            cursor = j.get("nextCursor") if j.get("hasMore") else None
            if not cursor:
                return rows


def reason_of(detail: dict) -> str:
    """The reviewer's "title: description" of each rejected video, one per line ("" when none is given)."""
    out = []
    for f in detail.get("files") or []:
        flag = f.get("flag") or {}
        line = ": ".join(x.strip() for x in (flag.get("title"), flag.get("description")) if x and x.strip())
        if line and line not in out:
            out.append(line)
    return "\n".join(out)[:4000]


async def fetch_reason_texts(settings: Settings, todo: list[tuple[str, str]]) -> dict[str, str]:
    """recording id → reason, for (recording id, UseMinute user id) pairs. One that fails is left for the next sync."""
    found: dict[str, str] = {}
    sem = asyncio.Semaphore(REASON_WORKERS)
    async with _http() as http:
        token = await _token(http, settings)

        async def one(recording_id: str, user_key: str) -> None:
            async with sem:
                try:
                    detail = await _get(http, f"{_org_url(settings)}/users/{user_key}/sessions/{recording_id}", {}, token)
                    found[recording_id] = reason_of(detail)
                except Exception as e:
                    log.warning("No UseMinute reason for recording %s: %s", recording_id, type(e).__name__)

        await asyncio.gather(*(one(rid, key) for rid, key in todo))
    return found


async def fetch_accounts(settings: Settings, beat=None) -> list[dict]:
    """Every account in the organisation on UseMinute (its users list), active or not."""
    out, offset = [], 0
    async with _http() as http:
        token = await _token(http, settings)
        while True:
            j = await _get(http, _org_url(settings) + "/users", {"limit": str(ACCOUNTS_PAGE), "offset": str(offset)}, token)
            out += j.get("users") or []
            if beat is not None:
                await beat()
            if not j.get("hasMore") or j.get("nextOffset") in (None, offset):
                return out
            offset = int(j["nextOffset"])


def normalize_account(row: dict) -> dict | None:
    email = (row.get("email") or "").strip().lower()
    if not email or len(email) > 254:
        return None
    try:
        joined, last = _time(row.get("joinedAt") or row.get("createdAt")), _time(row.get("lastUploadCreatedAt"))
    except ValueError:
        joined = last = None
    return {
        "email": email,
        "user_key": _cut(row.get("resourceKey"), 64),
        "active": not row.get("disabled") and (row.get("userState") or "active").lower() == "active",
        "joined_at": joined,
        "last_upload_at": last,
        "uploads": _int(row.get("uploadCount")) or 0,
        "duration_ms": _int(row.get("totalDurationMs")) or 0,
    }


async def video(settings: Settings, recording: Recording) -> list[dict]:
    """Fresh links to a recording's videos (UseMinute's links expire after about an hour), flagged ones
    first, each with the reviewer's reason."""
    if not settings.doppi_configured:
        raise DoppiError("UseMinute sync is not set up on the server")
    if not recording.user_key:
        raise DoppiError("This recording can't be opened yet. Try again after the next sync.")
    async with _http() as http:
        token = await _token(http, settings)
        detail = await _get(http, f"{_org_url(settings)}/users/{recording.user_key}/sessions/{recording.id}", {}, token)
    files = []
    for f in detail.get("files") or []:
        if not f.get("url"):
            continue
        flag = f.get("flag") or {}
        reason = ": ".join(x.strip() for x in (flag.get("title"), flag.get("description")) if x and x.strip())
        files.append({"url": f["url"], "flagged": bool(flag), "reason": reason or None})
    files.sort(key=lambda f: not f["flagged"])
    return files


# ---- storing


async def store_accounts(rows: list[dict]) -> int:
    """Refresh the account list: listed accounts are added or updated, unlisted ones become inactive.
    Returns how many are active."""
    now = clock.now()
    rows = list({r["email"]: r for r in rows}.values())
    async with db.WriteSession() as session:
        for i in range(0, len(rows), STORE_CHUNK):
            chunk = [{**r, "synced_at": now} for r in rows[i : i + STORE_CHUNK]]
            stmt = insert(DoppiAccount).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[DoppiAccount.email],
                set_={c: stmt.excluded[c] for c in ("user_key", "active", "joined_at", "last_upload_at", "uploads",
                                                     "duration_ms", "synced_at")},
            )
            await session.execute(stmt)
        if rows:  # an empty answer is more likely a hiccup than an empty organisation
            await session.execute(update(DoppiAccount).where(DoppiAccount.synced_at < now).values(active=False))
        await session.commit()
    return sum(1 for r in rows if r["active"])



async def store(rows: list[dict], beat=None) -> Stats:
    """Add new recordings and update known ones (the review status changes for weeks). Works in small
    transactions, so people using the app are never kept waiting for long."""
    stats = Stats(recordings=len(rows), clock_fixed=sum(r["clock_fixed"] for r in rows))
    now = clock.now()
    rows = list({r["id"]: r for r in rows}.values())
    for i in range(0, len(rows), STORE_CHUNK):
        chunk = [{**r, "first_seen_at": now, "synced_at": now} for r in rows[i : i + STORE_CHUNK]]
        async with db.WriteSession() as session:
            known = set(await session.scalars(select(Recording.id).where(Recording.id.in_([r["id"] for r in chunk]))))
            stats.new += sum(1 for r in chunk if r["id"] not in known)
            stmt = insert(Recording).values(chunk)
            still_rejected = stmt.excluded.status == REC_REJECTED
            stmt = stmt.on_conflict_do_update(
                index_elements=[Recording.id],
                set_={c: stmt.excluded[c] for c in UPDATED}
                | {
                    # A recording that is no longer rejected loses its reason, and a later rejection is news again
                    "rejection_reason": case((still_rejected, Recording.rejection_reason), else_=None),
                    "rejection_notified_at": case((still_rejected, Recording.rejection_notified_at), else_=None),
                },
            )
            await session.execute(stmt)
            await session.commit()
        if beat is not None:
            await beat()
    emails = sorted({r["email"] for r in rows})
    stats.phones = len(emails)
    for i in range(0, len(emails), 400):
        chunk = emails[i : i + 400]
        names = ", ".join(f":e{n}" for n in range(len(chunk)))
        async with db.WriteSession() as session:
            await session.execute(
                text(f"""
                    INSERT INTO phones (email, model, device_type, first_recorded_at, last_recorded_at, recordings)
                    SELECT r.email,
                           (SELECT x.phone_model FROM recordings x WHERE x.email = r.email AND x.phone_model IS NOT NULL
                            ORDER BY x.recorded_at DESC LIMIT 1),
                           (SELECT x.device_type FROM recordings x WHERE x.email = r.email AND x.device_type IS NOT NULL
                            ORDER BY x.recorded_at DESC LIMIT 1),
                           MIN(r.recorded_at), MAX(r.recorded_at), COUNT(*)
                    FROM recordings r WHERE r.email IN ({names})
                    GROUP BY r.email
                    ON CONFLICT (email) DO UPDATE SET
                        model = COALESCE(excluded.model, phones.model),
                        device_type = COALESCE(excluded.device_type, phones.device_type),
                        first_recorded_at = excluded.first_recorded_at,
                        last_recorded_at = excluded.last_recorded_at,
                        recordings = excluded.recordings
                """),
                {f"e{n}": email for n, email in enumerate(chunk)},
            )
            await session.commit()
    return stats


async def store_reasons(settings: Settings, beat=None) -> int:
    """Look up UseMinute's reason for every rejected recording that doesn't have one yet."""
    async with db.ReadSession() as session:
        q = (
            select(Recording.id, Recording.user_key)
            .where(Recording.status == REC_REJECTED, Recording.rejection_reason.is_(None), Recording.user_key.is_not(None))
            .order_by(Recording.recorded_at.desc())
            .limit(REASONS_PER_SYNC)
        )
        todo = [(rid, key) for rid, key in (await session.execute(q)).all()]
    done = 0
    for i in range(0, len(todo), 100):
        found = await fetch_reason_texts(settings, todo[i : i + 100])
        async with db.WriteSession() as session:
            for recording_id, reason in found.items():
                await session.execute(update(Recording).where(Recording.id == recording_id).values(rejection_reason=reason))
            await session.commit()
        done += len(found)
        if beat is not None:
            await beat()
    return done


# ---- the sync itself


def _fingerprint(settings: Settings) -> str:
    return hashlib.sha256(settings.doppi_refresh_token.encode()).hexdigest()[:16]


async def _save(**values) -> None:
    async with db.WriteSession() as session:
        await session.execute(update(SyncState).where(SyncState.id == 1).values(**values))
        await session.commit()


async def _take_lock(token: str) -> bool:
    now = clock.now()
    async with db.WriteSession() as session:
        free = or_(SyncState.lock_token.is_(None), SyncState.lock_at.is_(None), SyncState.lock_at < now - LOCK_STALE)
        result = await session.execute(
            update(SyncState).where(SyncState.id == 1, free).values(lock_token=token, lock_at=now, started_at=now)
        )
        await session.commit()
        return result.rowcount == 1


async def is_running(session: AsyncSession) -> bool:
    state = await session.get(SyncState, 1)
    return bool(state and state.lock_token and state.lock_at and state.lock_at > clock.now() - LOCK_STALE)


async def run(settings: Settings, days: int | None = None) -> Stats:
    """Download and store. Raises SyncBusy when a sync is already running (in any process), LoginRefused
    when UseMinute refuses the login, DoppiError for anything else. The outcome is saved for Settings (SET-12)."""
    if not settings.doppi_configured:
        raise DoppiError("UseMinute sync is not set up: DOPPI_API_KEY, DOPPI_REFRESH_TOKEN and DOPPI_ORG are missing in the server configuration")
    token = secrets.token_hex(8)
    if not await _take_lock(token):
        raise SyncBusy("A UseMinute sync is already running")

    async def beat() -> None:
        async with db.WriteSession() as session:
            await session.execute(update(SyncState).where(SyncState.id == 1, SyncState.lock_token == token).values(lock_at=clock.now()))
            await session.commit()

    try:
        return await _run(settings, days, beat)
    finally:
        async with db.WriteSession() as session:
            await session.execute(update(SyncState).where(SyncState.id == 1, SyncState.lock_token == token).values(lock_token=None))
            await session.commit()


async def _run(settings: Settings, days: int | None, beat) -> Stats:
    try:
        async with db.ReadSession() as session:
            first = (await session.get(SyncState, 1)).success_at is None
        window = days or (settings.doppi_initial_days if first else settings.doppi_sync_days)
        stats_accounts = await store_accounts([a for r in await fetch_accounts(settings, beat) if (a := normalize_account(r))])
        raw = await fetch_report(settings, window, beat)
        rows = [n for r in raw if (n := normalize(r, settings.tz)) is not None]
        stats = await store(rows, beat)
        stats.days = window
        stats.accounts = stats_accounts
        stats.reasons = await store_reasons(settings, beat)
    except LoginRefused as e:
        await _save(finished_at=clock.now(), error=str(e), refused_login=_fingerprint(settings))
        raise
    except DoppiError as e:
        await _save(finished_at=clock.now(), error=str(e))
        raise
    except httpx.HTTPStatusError as e:
        message = f"UseMinute answered with an error ({e.response.status_code})"
        await _save(finished_at=clock.now(), error=message)
        raise DoppiError(message) from e
    except Exception as e:
        log.exception("UseMinute sync failed")
        message = f"UseMinute sync failed: {type(e).__name__}"
        await _save(finished_at=clock.now(), error=message)
        raise DoppiError(message) from e
    now = clock.now()
    await _save(finished_at=now, success_at=now, error=None, refused_login=None, stats=asdict(stats))
    log.info("UseMinute sync: %s recordings (%s new), %s phones", stats.recordings, stats.new, stats.phones)
    return stats


async def due(settings: Settings) -> bool:
    """A scheduled sync time has passed and no sync has succeeded since. A failed run is retried after an
    hour; a refused login is not retried until the login in the configuration changes (EXT-11)."""
    if not settings.doppi_configured or not settings.sync_times:
        return False
    now = clock.now().astimezone(settings.tz)
    slots = [datetime.combine(now.date() - timedelta(days=back), time.fromisoformat(t), settings.tz)
             for back in (0, 1) for t in settings.sync_times]
    slot = max(s for s in slots if s <= now)
    async with db.ReadSession() as session:
        state = await session.get(SyncState, 1)
    if state.refused_login and state.refused_login == _fingerprint(settings):
        return False
    if state.success_at is not None and state.success_at >= slot:
        return False
    return state.started_at is None or clock.now() - state.started_at >= RETRY_AFTER


async def is_fresh(session: AsyncSession) -> bool:
    """A sync succeeded in the last few minutes, so another one would bring nothing new."""
    state = await session.get(SyncState, 1)
    return state.success_at is not None and clock.now() - state.success_at < FRESH


async def last_updated(session: AsyncSession) -> datetime | None:
    """When UseMinute data was last updated: shown on every list screen (APP-7)."""
    return await session.scalar(select(SyncState.success_at).where(SyncState.id == 1))


async def status(session: AsyncSession, settings: Settings) -> dict:
    """SET-12: when data was last synced, whether that worked, and the last error if it did not."""
    state = await session.get(SyncState, 1)
    return {
        "configured": settings.doppi_configured,
        "running": await is_running(session),
        "last_sync_at": state.finished_at.isoformat() if state.finished_at else None,
        "last_success_at": state.success_at.isoformat() if state.success_at else None,
        "ok": state.error is None,
        "error": state.error,
        "stats": state.stats,
        "schedule": settings.sync_times,
    }


ACCOUNT_FILTERS = ("linked", "not_linked", "off")


async def accounts(session: AsyncSession, *, q: str = "", flt: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """The UseMinute section (Admins): every account on UseMinute and the worker who linked it, if anyone
    has. Active accounts nobody has linked come first, since those are the people still to connect."""
    links = {email: (uid, name) for email, uid, name in (await session.execute(
        select(DoppiLink.email, User.id, User.full_name).join(User, User.id == DoppiLink.user_id).where(DoppiLink.unlinked_at.is_(None))
    )).all()}
    models = dict((await session.execute(select(Phone.email, Phone.model))).all())
    rows, counts = [], {"all": 0, "linked": 0, "not_linked": 0, "off": 0}
    needle = q.strip().lower()
    for a in await session.scalars(select(DoppiAccount)):
        link = links.get(a.email)
        kind = "off" if not a.active else "linked" if link else "not_linked"
        counts[kind] += 1
        counts["all"] += a.active
        if (flt in ACCOUNT_FILTERS and kind != flt) or (flt not in ACCOUNT_FILTERS and not a.active):
            continue
        if needle and needle not in a.email and not (link and needle in link[1].lower()):
            continue
        rows.append({
            "email": a.email,
            "active": a.active,
            "model": models.get(a.email),
            "uploads": a.uploads,
            "minutes": a.duration_ms / 60_000,
            "last_upload_at": a.last_upload_at.isoformat() if a.last_upload_at else None,
            "joined_at": a.joined_at.isoformat() if a.joined_at else None,
            "user": {"id": link[0], "name": link[1]} if link else None,
        })
    rows.sort(key=lambda r: (not r["active"], r["user"] is not None, -(datetime.fromisoformat(r["last_upload_at"]).timestamp() if r["last_upload_at"] else 0), r["email"]))
    return {"counts": counts, "total": len(rows), "rows": rows[offset : offset + limit], "has_more": len(rows) > offset + limit}
