"""Background retry for replies that never went out.

The normal path is delivery right after the commit (bot/middlewares.py): the
user gets the reply synchronously, the outbox row is deleted, and nothing
reaches this module. It exists for the case the normal path cannot cover — a
row that outlived the process. Deploys kill the bot on purpose, and an update
whose transaction committed a moment before SIGTERM would otherwise go
unanswered: the ride is recorded and the user does not know it.

The guarantee is at-least-once. A duplicated reply is harmless; a lost one is
not, because the user repeats the entry and the ride is logged twice.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage, TelegramMethod
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from chain_health.db.engine import READONLY
from chain_health.db.models import OutboxMessage
from chain_health.timeutils import utcnow

logger = logging.getLogger(__name__)

# How often to look at the queue. Normally it is empty and the indexed SELECT
# costs nothing.
POLL_INTERVAL = 5.0
# Rows per pass, so a long queue does not hold the write lock in one go.
BATCH = 20
# How often to sweep expired rows. Not on every tick: that is a write under the
# process-wide lock, and the table is normally empty.
PURGE_EVERY = timedelta(hours=1)
# Retry spacing: a minute, two, four… capped at an hour.
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_MAX = timedelta(hours=1)
# When to give up. A day-old reply is of no use to the user, and the table must
# not grow forever.
TTL = timedelta(days=1)

# Only what we put there ourselves is revived (see db.models.OutboxMessage).
# An explicit map rather than a lookup by name: the row comes out of the
# database and must not be able to name an arbitrary Bot API method.
#
# Edits are absent on purpose (see ui.Responder.edit): a deferred edit returns
# the user to a screen they have already left.
_METHODS: dict[str, type[TelegramMethod]] = {
    SendMessage.__name__: SendMessage,
}


def _revive(row_id: int, method: str, payload: str) -> TelegramMethod | None:
    """The aiogram method behind a row, or None if the row is beyond saving."""
    factory = _METHODS.get(method)
    if factory is None:
        logger.error("Outbox row %s names an unknown method %r", row_id, method)
        return None
    try:
        return factory.model_validate_json(payload)
    except Exception:
        # The payload format drifted incompatibly (or the row was hand-edited).
        # Keeping it is pointless — it will never be sendable.
        logger.exception("Could not parse outbox row %s", row_id)
        return None


@dataclass(slots=True)
class _Outcome:
    """The fate of a sent batch that has not been written to the DB yet.

    Until it is written, sending more is FORBIDDEN: rows in ``done`` are
    delivered but still queued, and the next pass would send them again — once
    per tick until the database becomes writable. At-least-once would degrade
    into an unbounded duplicate loop.
    """

    done: list[int] = field(default_factory=list)
    retry: list[tuple[int, int, object]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.done or self.retry)


async def _has_due(engine: AsyncEngine) -> bool:
    """Any rows due — WITHOUT the lock and without a write transaction.

    An empty poll tick used to cost BEGIN IMMEDIATE under the process-wide
    lock, ~17k write transactions a day for a normally-empty table; while an
    outside writer held the file, every tick also stalled user updates for
    busy_timeout. A READONLY read under WAL touches no write lock at all.
    """
    async with engine.connect() as conn:
        ro = await conn.execution_options(**{READONLY: True})
        found = await ro.scalar(
            select(OutboxMessage.id).where(OutboxMessage.next_attempt_at <= utcnow()).limit(1)
        )
    return found is not None


async def _apply_outcome(
    session_factory: async_sessionmaker[AsyncSession], lock: asyncio.Lock, outcome: _Outcome
) -> None:
    """Write the batch's fate: delete the delivered, back off the failed."""
    async with lock, session_factory() as session:
        if outcome.done:
            await session.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(outcome.done)))
        # One UPDATE per group rather than one SELECT+UPDATE per row: the rows
        # were already read at selection time, and up to BATCH extra round
        # trips would hold the process-wide lock exactly when the queue is full
        # after an outage.
        for (when, attempts), ids in _grouped(outcome.retry).items():
            await session.execute(
                update(OutboxMessage)
                .where(OutboxMessage.id.in_(ids))
                .values(next_attempt_at=when, attempts=attempts)
            )
        await session.commit()


async def deliver_batch(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession], lock: asyncio.Lock
) -> tuple[int, _Outcome | None]:
    """One pass over the queue: (messages sent, unwritten outcome).

    Outcome None means everything is recorded; otherwise the caller MUST write
    it before sending anything else (see _Outcome) — run_sender does exactly
    that, and tests see the failure explicitly instead of a swallowed log line.

    Three steps, and the split is not cosmetic: read under the lock, send
    WITHOUT it, write the outcome back under the lock. Sending under the lock
    would make this task reproduce the very defect the whole design removes,
    holding the database for a full round trip to Telegram.
    """
    now = utcnow()
    async with lock, session_factory() as session:
        rows = list(
            await session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.next_attempt_at <= now)
                .order_by(OutboxMessage.id)
                .limit(BATCH)
            )
        )
        # Taken as values, not ORM objects: the session closes before the
        # network call, and touching a detached row afterwards would fail.
        pending = [(r.id, r.method, r.payload, r.created_at, r.attempts) for r in rows]
    if not pending:
        return 0, None

    outcome = _Outcome()
    done = outcome.done  # delivered or hopeless — deleted either way
    retry = outcome.retry  # id, attempt, when to try again
    sent = 0
    for row_id, method_name, payload, created_at, attempts in pending:
        if utcnow() - created_at > TTL:
            logger.warning("Outbox row %s expired (%s) — dropped", row_id, method_name)
            done.append(row_id)
            continue
        method = _revive(row_id, method_name, payload)
        if method is None:
            done.append(row_id)
            continue
        try:
            await bot(method)
        except TelegramRetryAfter as exc:
            # Telegram said when to come back — no argument.
            retry.append((row_id, attempts + 1, utcnow() + timedelta(seconds=exc.retry_after)))
        except Exception as exc:
            delay = min(BACKOFF_BASE * 2 ** (attempts + 1), BACKOFF_MAX)
            retry.append((row_id, attempts + 1, utcnow() + delay))
            logger.warning("Retry of %s failed (attempt %s): %s", row_id, attempts + 1, exc)
        else:
            done.append(row_id)
            sent += 1
            logger.info("Deferred delivery of %s (%s) succeeded", row_id, method_name)

    try:
        await _apply_outcome(session_factory, lock, outcome)
    except Exception:
        logger.exception("Batch outcome not recorded — sending paused until it is")
        return sent, outcome
    return sent, None


def _grouped(retry: list[tuple[int, int, object]]) -> dict[tuple[object, int], list[int]]:
    """Rows sharing a retry time and attempt count — usually one or two groups."""
    groups: dict[tuple[object, int], list[int]] = {}
    for row_id, attempts, when in retry:
        groups.setdefault((when, attempts), []).append(row_id)
    return groups


async def purge_expired(
    session_factory: async_sessionmaker[AsyncSession], lock: asyncio.Lock
) -> None:
    """Drop what expired without deliver_batch ever looking at it.

    A row with a far-off next_attempt_at never enters the batch, yet it can
    still go stale — without this it would sit in the table forever.
    """
    async with lock, session_factory() as session:
        await session.execute(
            delete(OutboxMessage).where(OutboxMessage.created_at < utcnow() - TTL)
        )
        await session.commit()


async def run_sender(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    lock: asyncio.Lock,
    *,
    interval: float = POLL_INTERVAL,
) -> None:
    """The loop. Cancelled together with polling when the bot stops.

    ``lock`` is the same write lock the middleware takes: SQLite has one writer
    per process, and a background task is no exception.
    """
    purged_at = utcnow() - PURGE_EVERY
    carry: _Outcome | None = None
    while True:
        try:
            if carry:
                # Record the fate of the batch that ALREADY went out before
                # sending anything new: its delivered rows still look queued,
                # and another pass would deliver them again — once per tick
                # until the database comes back.
                await _apply_outcome(session_factory, lock, carry)
                carry = None
            if utcnow() - purged_at >= PURGE_EVERY:
                await purge_expired(session_factory, lock)
                purged_at = utcnow()
            # An empty queue is the normal state, and finding that out must be
            # free: READONLY read, no lock — see _has_due.
            if await _has_due(engine):
                _sent, carry = await deliver_batch(bot, session_factory, lock)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Staying alive matters more: the queue gets another pass shortly.
            # carry survives — if the write itself failed, the next pass
            # starts with it.
            logger.exception("Outbox sender failed")
        await asyncio.sleep(interval)
