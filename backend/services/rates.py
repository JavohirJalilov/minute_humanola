"""The pay rate (section 10.2): so'm per hour of accepted recording.

There is one company-wide rate; a staff member or a single worker can have their own, and the most
specific wins (SET-9). A rate has a start date and rows are only added, so each recording keeps the rate
that was in force on its day (CALC-6, SET-8).
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock, roles
from backend.config import Settings
from backend.errors import AppError, not_found
from backend.models import SCOPE_COMPANY, SCOPE_STAFF, SCOPE_WORKER, DoppiLink, Rate, User
from backend.services import amounts, audit, notify, people

RATE_MIN, RATE_MAX = 1, 10_000_000
SCOPES = (SCOPE_COMPANY, SCOPE_STAFF, SCOPE_WORKER)


@dataclass
class RateChange:
    rate: Rate
    # worker id → their rate from the start date on (None: no rate): only workers whose rate changed (SET-11)
    affected: dict[int, int | None]


async def _linked_workers(session: AsyncSession) -> list[int]:
    return list(await session.scalars(select(DoppiLink.user_id).where(DoppiLink.unlinked_at.is_(None))))


async def add(session: AsyncSession, settings: Settings, actor: User, *, scope: str, user_id: int | None = None,
              amount: int | None, start_date: date) -> RateChange:
    """Save a new rate from `start_date`. For a staff member or worker, no amount ends their own rate
    from that date, so the wider one applies again. Commits."""
    people.require_admin(actor, settings)
    if scope not in SCOPES:
        raise AppError("rate_scope", "Choose who the rate is for")
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or not RATE_MIN <= amount <= RATE_MAX):
        raise AppError("rate_invalid", "The rate must be a whole number between 1 and 10 000 000 so'm per hour")  # SET-7
    if scope == SCOPE_COMPANY:
        user_id = None
        if amount is None:
            raise AppError("rate_invalid", "The rate must be a whole number between 1 and 10 000 000 so'm per hour")
    else:
        target = await session.get(User, user_id) if user_id is not None else None
        if target is None:
            raise not_found("This person was not found")
        if scope == SCOPE_STAFF and not roles.is_staff(target, settings):
            raise AppError("not_staff", "This person is not a staff member")

    workers = await _linked_workers(session)
    before = await amounts.RateBook.load(session, settings.tz, workers)
    if amount is None:
        timeline = (before.by_staff if scope == SCOPE_STAFF else before.by_worker).get(user_id, [])
        if amounts.latest(timeline, start_date) is None:
            raise AppError("rate_nothing_to_end", "This person has no rate of their own on that date", 409)

    rate = Rate(scope=scope, user_id=user_id, amount=amount, start_date=start_date, created_by_id=actor.id, created_at=clock.now())
    session.add(rate)
    await session.flush()
    after = await amounts.RateBook.load(session, settings.tz, workers)
    affected = {w: after.rate_on(w, start_date) for w in workers if before.rate_on(w, start_date) != after.rate_on(w, start_date)}
    audit.record(session, actor.id, "settings.rate.added", target_user_id=user_id, entity="rate", entity_id=rate.id,
                 scope=scope, amount=amount, start_date=start_date)
    await notify.rate_changed(session, settings, affected, start_date)  # SET-11
    await session.commit()
    return RateChange(rate, affected)


async def listing(session: AsyncSession, settings: Settings) -> dict:
    """The company rate with its history, and each staff member's or worker's own rate in force or coming."""
    today = settings.today()
    rows = list(await session.scalars(select(Rate).order_by(Rate.start_date.desc(), Rate.id.desc())))
    ids = {r.user_id for r in rows if r.user_id} | {r.created_by_id for r in rows if r.created_by_id}
    names = dict((await session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))).all()) if ids else {}

    def out(r: Rate) -> dict:
        return {"id": r.id, "amount": r.amount, "start_date": r.start_date.isoformat(),
                "created_by": names.get(r.created_by_id), "created_at": r.created_at.isoformat()}

    company = [r for r in rows if r.scope == SCOPE_COMPANY]
    current = next((r for r in company if r.start_date <= today), None)
    own: dict[tuple[str, int], list[Rate]] = {}
    for r in rows:
        if r.scope != SCOPE_COMPANY:
            own.setdefault((r.scope, r.user_id), []).append(r)
    overrides = []
    for (scope, user_id), history in own.items():
        in_force = next((r for r in history if r.start_date <= today), None)
        upcoming = [r for r in history if r.start_date > today]
        if (in_force is None or in_force.amount is None) and not upcoming:
            continue  # ended, and nothing planned
        overrides.append({
            "scope": scope,
            "user": {"id": user_id, "name": names.get(user_id, "")},
            "amount": in_force.amount if in_force else None,
            "start_date": in_force.start_date.isoformat() if in_force else None,
            "upcoming": [out(r) for r in reversed(upcoming)],
        })
    overrides.sort(key=lambda o: (o["scope"] != SCOPE_STAFF, o["user"]["name"].lower()))
    return {
        "company": out(current) if current else None,
        "upcoming": [out(r) for r in reversed(company) if r.start_date > today],
        "history": [out(r) for r in company],
        "overrides": overrides,
        "min": RATE_MIN,
        "max": RATE_MAX,
    }
