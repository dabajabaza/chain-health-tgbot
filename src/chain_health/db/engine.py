from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# aiogram processes updates as concurrent tasks (see docs/ARCHITECTURE.md);
# each gets its own request-scoped session/connection to the same SQLite
# file, and a handler holds its write lock across a few Telegram round-trips.
# Without a busy_timeout, a second concurrent writer gets an immediate
# "database is locked" OperationalError instead of waiting. WAL also lets
# readers proceed without blocking on a writer.
_BUSY_TIMEOUT_MS = 10_000


def create_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
