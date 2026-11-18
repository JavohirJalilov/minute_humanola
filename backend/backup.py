"""Daily database backups (NFR-12).

A backup is a complete copy of the SQLite file made with SQLite's online backup, which is safe while the
app is running. Files go to BACKUP_DIR and are kept BACKUP_KEEP_DAYS days.

    python -m backend.backup                 make a backup now
    python -m backend.backup verify <file>   check that a backup opens, is intact, and what it holds

To restore: stop the backend, put the backup file in place of the database file (remove the old
`-wal` and `-shm` files next to it), start the backend.
"""

import asyncio
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from backend import clock, db
from backend.config import Settings, get_settings

COUNTED = ("users", "recordings", "transfers", "doppi_links", "audit_log")


def copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def verify(path: Path) -> dict:
    """Open a backup the way a restore would: integrity check, and how many rows the main tables have."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"The backup is damaged: {result}")
        return {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}
    finally:
        con.close()


def prune(directory: Path, keep_days: int) -> int:
    limit = clock.now().timestamp() - timedelta(days=keep_days).total_seconds()
    removed = 0
    for f in directory.glob("doppi-*.db"):
        if f.stat().st_mtime < limit:
            f.unlink()
            removed += 1
    return removed


async def run(settings: Settings) -> Path:
    """Make a backup now, check it, and drop the ones that are too old."""
    stamp = clock.now().astimezone(settings.tz).strftime("%Y%m%d-%H%M%S")
    target = Path(settings.backup_dir) / f"doppi-{stamp}.db"
    await asyncio.to_thread(copy, db.database_path, target)
    await asyncio.to_thread(verify, target)
    await asyncio.to_thread(prune, target.parent, settings.backup_keep_days)
    return target


if __name__ == "__main__":
    settings = get_settings()
    if len(sys.argv) >= 3 and sys.argv[1] == "verify":
        print(verify(Path(sys.argv[2])))
    else:
        db.init_engine(settings.database_url)
        print(asyncio.run(run(settings)))
