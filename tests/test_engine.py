from sqlalchemy import text

from chain_health.db.engine import create_engine


async def test_engine_sets_wal_and_a_generous_busy_timeout(tmp_path):
    """Concurrent request-scopes each get their own connection to the same
    SQLite file, and a handler can hold a write lock across a few Telegram
    round-trips (see docs/ARCHITECTURE.md #9) — without these pragmas, a
    second writer gets "database is locked" instead of waiting.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'engine_test.db'}")
    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        assert journal_mode == "wal"
        assert busy_timeout == 10_000
    finally:
        await engine.dispose()
