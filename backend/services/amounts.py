"""How amounts are calculated (section 8.1).

Nothing here is stored: every figure is derived on request from the recordings UseMinute reported, the rate
history, and the transfers people confirmed (NFR-9).

    Earned         = accepted hours × the rate in force on each recording day
    Pending review = pending hours × rate
    Rejected       = rejected hours × rate
    Paid           = worker payments the worker accepted
    Remaining      = Earned − Paid   (shown as Overpaid when Paid is larger)

All arithmetic is in whole numbers: recording time in milliseconds, rates and money in so'm. Time is
never rounded to hours; each money amount is rounded to a whole so'm once, at the very end (CALC-1).
"""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings
from backend.models import (
    ACCEPTED,
    AWAITING,
    FUNDING,
    PAYMENT,
    REC_ACCEPTED,
    REC_PENDING,
    REC_REJECTED,
    SCOPE_COMPANY,
    SCOPE_STAFF,
    SCOPE_WORKER,
    DoppiLink,
    Rate,
    StaffAssignment,
    Transfer,
)

STATUSES = (REC_ACCEPTED, REC_PENDING, REC_REJECTED)
MS_PER_HOUR = 3_600_000
MS_PER_MINUTE = 60_000
CHUNK = 2000  # rate stretches per query: well inside SQLite's limit on bound values


def som(ms_times_rate: int) -> int:
    """Milliseconds × so'm per hour → whole so'm, rounded half up."""
    return (2 * ms_times_rate + MS_PER_HOUR) // (2 * MS_PER_HOUR)


def amount_for(duration_ms: int, rate: int | None) -> int | None:
    return som(duration_ms * rate) if rate is not None else None


@dataclass
class Split:
    """Recording time by review status, and the same time weighted by the rate of each day."""

    ms: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))
    weighted: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))  # milliseconds × rate
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))
    unrated_ms: int = 0  # time recorded on days with no rate

    def add(self, other: "Split") -> None:
        for s in STATUSES:
            self.ms[s] += other.ms[s]
            self.weighted[s] += other.weighted[s]
            self.counts[s] += other.counts[s]
        self.unrated_ms += other.unrated_ms

    @property
    def total_ms(self) -> int:
        return sum(self.ms.values())

    def minutes(self, status: str) -> float:
        return self.ms[status] / MS_PER_MINUTE

    def amount(self, status: str) -> int:
        """So'm for one review status."""
        return som(self.weighted[status])


@dataclass(frozen=True)
class OwnedEmail:
    email: str
    current: bool  # linked now; False for an earlier email whose recordings still count (LNK-14)


async def owned_emails(session: AsyncSession, user_ids: Collection[int] | None = None) -> dict[int, list[OwnedEmail]]:
    """The UseMinute emails whose recordings count for each user: the one linked now, plus earlier ones that
    were unlinked with their recordings kept and that nobody else has linked since. An email counts for
    exactly one user: whoever linked it last."""
    counts = or_(DoppiLink.unlinked_at.is_(None), DoppiLink.keep_recordings)
    q = select(DoppiLink.email, DoppiLink.user_id, DoppiLink.unlinked_at, DoppiLink.verified_at, DoppiLink.id).where(counts)
    if user_ids is not None:
        mine = select(DoppiLink.email).where(DoppiLink.user_id.in_(list(user_ids)))
        q = q.where(DoppiLink.email.in_(mine))
    rows = (await session.execute(q)).all()
    # Per email: the current link wins, then the most recently verified
    rows.sort(key=lambda r: (r.email, r.unlinked_at is not None, -r.verified_at.timestamp(), -r.id))
    wanted = set(user_ids) if user_ids is not None else None
    out: dict[int, list[OwnedEmail]] = defaultdict(list)
    seen: set[str] = set()
    for r in rows:
        if r.email in seen:
            continue
        seen.add(r.email)
        if wanted is None or r.user_id in wanted:
            out[r.user_id].append(OwnedEmail(r.email, r.unlinked_at is None))
    for mine in out.values():
        mine.sort(key=lambda o: not o.current)
    return out


@dataclass(frozen=True)
class Segment:
    """Days start..end (end not included) on which one rate applies; no rate when `rate` is None."""

    start: date
    end: date
    rate: int | None


def latest(timeline, day: date) -> int | None:
    """The amount of the last entry that started on or before `day`."""
    found = None
    for start, amount in timeline:
        if start > day:
            break
        found = amount
    return found


class RateBook:
    """Every rate and, when staff rates exist, who each worker's staff member was on each day.
    Answers the rate in force for a worker on a day (SET-9): worker, then staff member, then company."""

    def __init__(self, company, by_staff, by_worker, periods):
        self.company: list[tuple[date, int | None]] = company
        self.by_staff: dict[int, list[tuple[date, int | None]]] = by_staff
        self.by_worker: dict[int, list[tuple[date, int | None]]] = by_worker
        # worker → [(first day, day after the last or None, staff id)]
        self.periods: dict[int, list[tuple[date, date | None, int]]] = periods

    @classmethod
    async def load(cls, session: AsyncSession, tz: ZoneInfo, worker_ids: Collection[int] | None = None) -> "RateBook":
        company, by_staff, by_worker = [], defaultdict(list), defaultdict(list)
        for r in await session.scalars(select(Rate).order_by(Rate.start_date, Rate.id)):
            if r.scope == SCOPE_COMPANY:
                company.append((r.start_date, r.amount))
            elif r.scope == SCOPE_STAFF:
                by_staff[r.user_id].append((r.start_date, r.amount))
            elif r.scope == SCOPE_WORKER:
                by_worker[r.user_id].append((r.start_date, r.amount))
        periods: dict[int, list] = defaultdict(list)
        if by_staff:  # assignments matter only when some staff member has their own rate
            q = select(StaffAssignment).order_by(StaffAssignment.worker_id, StaffAssignment.started_at, StaffAssignment.id)
            if worker_ids is not None and len(worker_ids) <= 500:
                q = q.where(StaffAssignment.worker_id.in_(list(worker_ids)))
            for a in await session.scalars(q):
                start = a.started_at.astimezone(tz).date()
                end = a.ended_at.astimezone(tz).date() if a.ended_at else None
                if end is None or end > start:  # replaced the same day: the later assignment has that day
                    periods[a.worker_id].append((start, end, a.staff_id))
            # A worker's first staff member also covers the days before the assignment was recorded:
            # recordings made before the worker registered belong to the team they turned out to be in
            for mine in periods.values():
                mine[0] = (date.min, mine[0][1], mine[0][2])
        return cls(company, dict(by_staff), dict(by_worker), dict(periods))

    def _staff_on(self, worker_id: int, day: date) -> int | None:
        for start, end, staff_id in self.periods.get(worker_id, ()):
            if start <= day and (end is None or day < end):
                return staff_id
        return None

    def rate_on(self, worker_id: int, day: date) -> int | None:
        amount = latest(self.by_worker.get(worker_id, ()), day)
        if amount is None:
            staff_id = self._staff_on(worker_id, day)
            if staff_id is not None:
                amount = latest(self.by_staff.get(staff_id, ()), day)
        if amount is None:
            amount = latest(self.company, day)
        return amount

    def segments(self, worker_id: int) -> list[Segment]:
        """The worker's whole timeline cut into stretches of one rate."""
        bounds = {date.min}
        bounds.update(start for start, _ in self.company)
        bounds.update(start for start, _ in self.by_worker.get(worker_id, ()))
        for start, end, staff_id in self.periods.get(worker_id, ()):
            if staff_id in self.by_staff:
                bounds.add(start)
                if end is not None:
                    bounds.add(end)
                bounds.update(s for s, _ in self.by_staff[staff_id] if s > start and (end is None or s < end))
        points = sorted(bounds)
        out: list[Segment] = []
        for i, start in enumerate(points):
            end = points[i + 1] if i + 1 < len(points) else date.max
            rate = self.rate_on(worker_id, start)
            if out and out[-1].rate == rate:
                out[-1] = Segment(out[-1].start, end, rate)
            else:
                out.append(Segment(start, end, rate))
        return out


async def earnings(
    session: AsyncSession,
    book: RateBook,
    owned: dict[int, list[OwnedEmail]],
    start: date | None = None,
    end: date | None = None,
) -> dict[int, dict[str, Split]]:
    """user id → email → recording time and money by review status, for recording days start..end.
    The database does the summing through the (email, day) index; a few rows per phone come back."""
    stretches = []  # (user id, email, first day, day after the last, rate)
    for user_id, mine in owned.items():
        segments = book.segments(user_id)
        for o in mine:
            for seg in segments:
                stretches.append((user_id, o.email, seg.start.isoformat(), seg.end.isoformat(), seg.rate))
    out: dict[int, dict[str, Split]] = {u: {o.email: Split() for o in mine} for u, mine in owned.items()}
    where, period = [], {}
    if start is not None:
        where.append("r.day >= :start")
        period["start"] = start.isoformat()
    if end is not None:
        where.append("r.day <= :end")
        period["end"] = end.isoformat()
    for i in range(0, len(stretches), CHUNK):
        chunk = stretches[i : i + CHUNK]
        params = dict(period)
        rows = []
        for n, (user_id, email, d_from, d_to, rate) in enumerate(chunk):
            rows.append(f"(:u{n}, :e{n}, :f{n}, :t{n}, :r{n})")
            params.update({f"u{n}": user_id, f"e{n}": email, f"f{n}": d_from, f"t{n}": d_to, f"r{n}": rate})
        sql = f"""
            WITH stretch(user_id, email, d_from, d_to, rate) AS (VALUES {", ".join(rows)})
            SELECT s.user_id, s.email, r.status,
                   SUM(r.duration_ms) AS ms,
                   COALESCE(SUM(r.duration_ms * s.rate), 0) AS weighted,
                   COALESCE(SUM(CASE WHEN s.rate IS NULL THEN r.duration_ms ELSE 0 END), 0) AS unrated,
                   COUNT(*) AS n
            FROM stretch s
            JOIN recordings r ON r.email = s.email AND r.day >= s.d_from AND r.day < s.d_to
            {"WHERE " + " AND ".join(where) if where else ""}
            GROUP BY s.user_id, s.email, r.status
        """
        for user_id, email, status, ms, weighted, unrated, n in (await session.execute(text(sql), params)).all():
            split = out[user_id][email]
            if status not in split.ms:
                status = REC_PENDING  # an unknown status is treated as pending (EXT-10)
            split.ms[status] += int(ms)
            split.weighted[status] += int(weighted)
            split.counts[status] += int(n)
            split.unrated_ms += int(unrated)
    return out


def total_of(phones: dict[str, Split]) -> Split:
    total = Split()
    for split in phones.values():
        total.add(split)
    return total


@dataclass
class Money:
    """A worker's money (section 8.1). Amounts are None while no rate applies to them (SET-10)."""

    split: Split
    paid: int = 0
    awaiting: int = 0  # payments sent to the worker and not answered yet
    rate: int | None = None  # the rate in force today
    phones: int = 0  # 1 when a UseMinute account is linked now

    @property
    def available(self) -> bool:
        """Money can be shown: a rate applies today, or applied to some of the recorded time."""
        return self.rate is not None or self.split.unrated_ms < self.split.total_ms

    @property
    def earned(self) -> int | None:
        return self.split.amount(REC_ACCEPTED) if self.available else None

    @property
    def pending_review(self) -> int | None:
        return self.split.amount(REC_PENDING) if self.available else None

    @property
    def rejected(self) -> int | None:
        return self.split.amount(REC_REJECTED) if self.available else None

    @property
    def remaining(self) -> int:
        """Earned − Paid, never negative (CALC-4). A payment awaiting confirmation does not reduce it (CALC-3)."""
        return max((self.earned or 0) - self.paid, 0)

    @property
    def overpaid(self) -> int:
        return max(self.paid - (self.earned or 0), 0)

    @property
    def payable(self) -> int:
        """What a new payment is pre-filled with: Remaining minus what already awaits confirmation (PAYS-6)."""
        return max(self.remaining - self.awaiting, 0)

    @property
    def rate_missing(self) -> bool:
        """Part of the recorded time falls on days without a rate, so it is in no amount."""
        return self.split.unrated_ms > 0


async def worker_transfers(session: AsyncSession, worker_ids: Collection[int] | None = None) -> dict[int, tuple[int, int]]:
    """worker id → (Paid, awaiting confirmation)."""
    q = (
        select(
            Transfer.receiver_id,
            func.coalesce(func.sum(Transfer.amount).filter(Transfer.state == ACCEPTED), 0),
            func.coalesce(func.sum(Transfer.amount).filter(Transfer.state == AWAITING), 0),
        )
        .where(Transfer.kind == PAYMENT)
        .group_by(Transfer.receiver_id)
    )
    if worker_ids is not None and len(worker_ids) <= 500:
        q = q.where(Transfer.receiver_id.in_(list(worker_ids)))
    return {uid: (int(paid), int(awaiting)) for uid, paid, awaiting in (await session.execute(q)).all()}


async def money_for(session: AsyncSession, settings: Settings, user_ids: Collection[int]) -> dict[int, Money]:
    """Earned, paid and remaining of each of these users, over all time."""
    ids = list(user_ids)
    if not ids:
        return {}
    # A long list is cheaper to read whole than to name id by id
    scope = ids if len(ids) <= 500 else None
    owned = await owned_emails(session, scope)
    wanted = set(ids)
    owned = {u: mine for u, mine in owned.items() if u in wanted}
    book = await RateBook.load(session, settings.tz, scope)
    earned = await earnings(session, book, owned)
    paid = await worker_transfers(session, scope)
    today = settings.today()
    out = {}
    for uid in ids:
        got, awaiting = paid.get(uid, (0, 0))
        out[uid] = Money(
            split=total_of(earned.get(uid, {})),
            paid=got,
            awaiting=awaiting,
            rate=book.rate_on(uid, today),
            phones=sum(1 for o in owned.get(uid, ()) if o.current),
        )
    return out


@dataclass
class Cash:
    """A staff member's cash position (PAYS-11)."""

    received: int = 0  # funding they confirmed
    paid_out: int = 0  # payments their workers accepted
    funding_awaiting: int = 0  # funding sent to them, not answered yet
    payments_awaiting: int = 0  # payments they sent, not answered yet

    @property
    def on_hand(self) -> int:
        return self.received - self.paid_out

    @property
    def available(self) -> int:
        """Cash on hand not already promised in a payment that awaits the worker's answer."""
        return self.on_hand - self.payments_awaiting


async def cash_for(session: AsyncSession, staff_ids: Collection[int]) -> dict[int, Cash]:
    ids = list(staff_ids)
    out = {uid: Cash() for uid in ids}
    if not ids:
        return out
    accepted = func.coalesce(func.sum(Transfer.amount).filter(Transfer.state == ACCEPTED), 0)
    awaiting = func.coalesce(func.sum(Transfer.amount).filter(Transfer.state == AWAITING), 0)
    q = select(Transfer.receiver_id, accepted, awaiting).where(Transfer.kind == FUNDING).group_by(Transfer.receiver_id)
    for uid, got, waiting in (await session.execute(q)).all():
        if uid in out:
            out[uid].received, out[uid].funding_awaiting = int(got), int(waiting)
    q = select(Transfer.sender_id, accepted, awaiting).where(Transfer.kind == PAYMENT).group_by(Transfer.sender_id)
    for uid, paid, waiting in (await session.execute(q)).all():
        if uid in out:
            out[uid].paid_out, out[uid].payments_awaiting = int(paid), int(waiting)
    return out


def needs_funding(to_be_paid: int, cash: Cash) -> int:
    """PAYA-2: To be paid, minus cash on hand, minus funding awaiting confirmation, never below zero."""
    return max(to_be_paid - cash.on_hand - cash.funding_awaiting, 0)
