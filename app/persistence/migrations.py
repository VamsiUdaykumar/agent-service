"""Tiny migration runner: applies un-applied `.sql` files in order, tracked
in `schema_migrations`. Plain SQL files are enough at this scale — no need
for Alembic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def apply_migrations(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()

    cursor = await conn.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        await conn.executescript(path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (path.name, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
