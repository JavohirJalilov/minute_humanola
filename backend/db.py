"""The SQLite database. Only the backend opens it; the bot goes through the API.

Two kinds of session on the same file:

- `ReadSession` starts a plain transaction: a consistent snapshot that never waits (WAL mode).
- `WriteSession` starts with BEGIN IMMEDIATE, so one writer runs at a time and everything it reads is
  current. That is what makes "check, then change" safe: a double tap or two simultaneous answers are
  handled one after the other, and the second sees what the first did (NFR-8).

Nothing slow (email, UseMinute, Telegram) may run while a write session is open.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from sqlalchemy import DateTime, MetaData, event, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateColumn
from sqlalchemy.types import TypeDecorator

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UTCDateTime(TypeDecorator):
    """A moment in time. SQLite has no timezone type, so it is stored as UTC and read back as UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Times must carry a timezone")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


read_engine: AsyncEngine | None = None
write_engine: AsyncEngine | None = None
ReadSession: async_sessionmaker[AsyncSession] | None = None
WriteSession: async_sessionmaker[AsyncSession] | None = None
database_path: Path | None = None


def _engine(url: str, begin: str) -> AsyncEngine:
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _):
        dbapi_conn.isolation_level = None  # we emit BEGIN ourselves, below
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # readers and the writer don't block each other
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=15000")  # a writer waits for the one before it
        cur.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql(begin)

    return engine


def init_engine(database_url: str) -> None:
    global read_engine, write_engine, ReadSession, WriteSession, database_path
    if not database_url.startswith("sqlite+aiosqlite:///"):
        raise RuntimeError("DATABASE_URL must be an SQLite file: sqlite+aiosqlite:///./data/doppi.db")
    database_path = Path(database_url.split(":///", 1)[1])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    read_engine = _engine(database_url, "BEGIN")
    write_engine = _engine(database_url, "BEGIN IMMEDIATE")
    ReadSession = async_sessionmaker(read_engine, expire_on_commit=False)
    WriteSession = async_sessionmaker(write_engine, expire_on_commit=False)


async def dispose() -> None:
    for engine in (read_engine, write_engine):
        if engine is not None:
            await engine.dispose()


# The audit log can't be edited or deleted, not even by a bug in this code (NFR-10)
AUDIT_GUARDS = (
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'the audit log cannot be changed'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'the audit log cannot be deleted'); END",
)


def _add_missing_columns(conn) -> None:
    """Add columns introduced after a table was created (create_all only makes new tables)."""
    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing and col.nullable:
                ddl = CreateColumn(col).compile(dialect=conn.dialect)
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')


async def create_tables() -> None:
    from backend import models  # noqa: F401  (register the tables)

    async with write_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
        for ddl in AUDIT_GUARDS:
            await conn.exec_driver_sql(ddl)
        await conn.exec_driver_sql("INSERT OR IGNORE INTO sync_state (id) VALUES (1)")


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Requests that only read get a read session; everything else gets the write session."""
    factory = ReadSession if request.method in ("GET", "HEAD") else WriteSession
    assert factory is not None, "init_engine() was not called"
    async with factory() as session:
        yield session
