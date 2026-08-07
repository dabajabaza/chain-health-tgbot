from typing import Any

from sqlalchemy import Connection, event
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
        # isolation_level = None disables pysqlite's own transaction handling.
        # By default the driver decides when to emit BEGIN (only right before
        # an INSERT/UPDATE/DELETE) and silently runs everything else in
        # autocommit — including SAVEPOINT. That breaks nested transactions:
        # `session.begin_nested()` emits its SAVEPOINT outside any real
        # transaction, so the matching RELEASE SAVEPOINT durably commits the
        # work, and a later `session.rollback()` finds nothing to undo. The
        # write survives a rollback that was supposed to discard it.
        #
        # docs/ARCHITECTURE.md D21 relies on exactly that nested transaction
        # (AccessService.ensure_registered wraps its insert in begin_nested so
        # a UNIQUE-constraint loss rolls back only the insert attempt) — with
        # driver-managed transactions the pattern would silently degrade to a
        # plain write. See tests/test_engine.py for the regression test.
        dbapi_connection.isolation_level = None

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _emit_begin(conn: Connection) -> None:
        # The other half of the recipe: with the driver no longer emitting
        # BEGIN, SQLAlchemy has to open the transaction itself, at the point
        # it considers a transaction to have started.
        #
        # IMMEDIATE, not a plain (deferred) BEGIN. A deferred transaction
        # takes its read snapshot at the first SELECT and only asks for the
        # write lock later, and SQLite refuses that upgrade outright — with
        # SQLITE_BUSY raised *immediately*, ignoring busy_timeout, because no
        # amount of waiting can rescue a snapshot another writer has already
        # invalidated. Two concurrent updates (D21) hit that on every
        # read-then-write handler. IMMEDIATE takes the write lock up front
        # instead, which is a lock the busy handler *can* wait on, so the
        # second writer queues for busy_timeout rather than failing. The cost
        # is that request scopes serialize; D1's single-writer bot can afford
        # it, and pysqlite's legacy mode was already doing much the same thing
        # by deferring its implicit BEGIN to the first DML statement.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
