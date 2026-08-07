from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chain_health.db.engine import create_engine
from chain_health.db.models import User


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


async def test_savepoint_is_undone_by_a_rollback_of_the_enclosing_transaction(
    session_factory: async_sessionmaker[AsyncSession],
):
    """The SAVEPOINT that D21 leans on has to be a real nested transaction.

    Under pysqlite's default (driver-managed) transaction handling the
    SAVEPOINT is emitted while the connection is still in autocommit mode, so
    `RELEASE SAVEPOINT` durably commits the insert and the enclosing
    `rollback()` has nothing left to undo — the row survives. That silently
    turns `AccessService.ensure_registered`'s nested transaction into a plain
    write, which is the opposite of what D21 promises.

    This also pins the test fixtures to `db.engine.create_engine`: build the
    engine any other way and the suite would be exercising different
    transaction semantics than production.
    """
    async with session_factory() as session:
        async with session.begin_nested():
            session.add(User(id=424_242))
            await session.flush()
        await session.rollback()

    async with session_factory() as verifier:
        assert await verifier.get(User, 424_242) is None
