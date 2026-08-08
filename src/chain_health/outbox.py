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
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage, TelegramMethod
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chain_health.db.models import OutboxMessage
from chain_health.timeutils import utcnow

logger = logging.getLogger(__name__)

# How often to look at the queue. Normally it is empty and the indexed SELECT
# costs nothing.
POLL_INTERVAL = 5.0
# Rows per pass, so a long queue does not hold the write lock in one go.
BATCH = 20
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


async def deliver_batch(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession], lock: asyncio.Lock
) -> int:
    """One pass over the queue. Returns how many messages went out.

    Three steps, and the split is not cosmetic: read under the lock, send
    WITHOUT it, write the outcome back under the lock. Sending under the lock
    would make this task reproduce the very defect the whole design removes,
    holding the database for a full round trip to Telegram.

    A function rather than the loop body so tests can call it directly, without
    starting a task and fighting the clock.
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
        return 0

    done: list[int] = []  # delivered or hopeless — deleted either way
    retry: list[tuple[int, int, object]] = []  # id, attempt, when to try again
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

    async with lock, session_factory() as session:
        if done:
            await session.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(done)))
        for row_id, attempts, when in retry:
            row = await session.get(OutboxMessage, row_id)
            if row is not None:
                row.attempts = attempts
                row.next_attempt_at = when  # type: ignore[assignment]
        await session.commit()
    return sent


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
    lock: asyncio.Lock,
    *,
    interval: float = POLL_INTERVAL,
) -> None:
    """The loop. Cancelled together with polling when the bot stops.

    ``lock`` is the same write lock the middleware takes: SQLite has one writer
    per process, and a background task is no exception.
    """
    while True:
        try:
            await purge_expired(session_factory, lock)
            await deliver_batch(bot, session_factory, lock)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Staying alive matters more: the queue gets another pass shortly.
            logger.exception("Outbox sender failed")
        await asyncio.sleep(interval)
