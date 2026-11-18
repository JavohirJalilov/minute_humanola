"""The audit log: every role change, staff assignment, block, link, unlink, settings change and transfer
state change, with who did it and when. Rows are only ever added."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend import clock
from backend.models import AuditLog


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def record(
    session: AsyncSession,
    actor_id: int | None,
    action: str,
    *,
    target_user_id: int | None = None,
    entity: str | None = None,
    entity_id: int | str | None = None,
    **details,
) -> None:
    """Add an entry in the caller's transaction, so it is saved exactly when the change is."""
    session.add(
        AuditLog(
            at=clock.now(),
            actor_id=actor_id,
            action=action,
            target_user_id=target_user_id,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=_plain(details),
        )
    )
