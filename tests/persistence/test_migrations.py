import aiosqlite

from app.persistence.migrations import apply_migrations


async def test_migrations_create_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "migrate.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await apply_migrations(conn)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}

    assert {"runs", "steps", "events", "schema_migrations"} <= tables


async def test_migrations_are_idempotent(tmp_path) -> None:
    db_path = tmp_path / "migrate.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await apply_migrations(conn)
        await apply_migrations(conn)  # must not raise (re-applying an already-applied file)

        cursor = await conn.execute("SELECT version FROM schema_migrations")
        versions = [row[0] for row in await cursor.fetchall()]

    assert versions == ["001_init.sql"]
