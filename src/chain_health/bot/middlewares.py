import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chain_health.bot.ui import Responder
from chain_health.db.models import OutboxMessage, ProcessedUpdate
from chain_health.services.access import AccessService
from chain_health.services.users import UserService
from chain_health.timeutils import utcnow

logger = logging.getLogger(__name__)

_MAX_TRACKED_IDS = 256


class _DenialLog:
    """Logs the first denial for a given id at WARNING, subsequent ones at DEBUG.

    Bounded so a spam bot can't grow this set forever; on overflow the whole
    set is cleared, which just means a burst of WARNINGs again — an acceptable
    trade-off for a personal bot that doesn't expect real traffic volume.
    """

    def __init__(self) -> None:
        self._seen: set[int] = set()

    def log(self, target: logging.Logger, key: int, message: str, *args: object) -> None:
        if len(self._seen) >= _MAX_TRACKED_IDS:
            self._seen.clear()
        if key in self._seen:
            target.debug(message, *args)
        else:
            self._seen.add(key)
            target.warning(message, *args)


_private_chat_denials = _DenialLog()
_auth_denials = _DenialLog()


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Drops every update that is not from a private chat (deny by default).

    Registered on ``dp.update.outer_middleware`` *before* ``setup_dishka``, so
    it runs before any dishka container or DB session is created for the
    update — group traffic never reaches ``AuthMiddleware.ensure_registered``.
    See docs/ARCHITECTURE.md D8: the rest of the app assumes chat_id == user_id.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat")
        if chat is None or chat.type != ChatType.PRIVATE:
            if chat is not None:
                _private_chat_denials.log(
                    logger,
                    chat.id,
                    "Ignoring update from non-private chat: chat_id=%s type=%s",
                    chat.id,
                    chat.type,
                )
            return None
        return await handler(event, data)


DELIVERY_SLOT = "delivery_slot"


class _DeliverySlot:
    """Carries the request scope's Responder back out to the flush.

    A mutable object, not a plain ``data`` key, because aiogram rebuilds the
    context dict between observers (``propagate_event(..., **kwargs)``): a key
    written by an inner middleware is invisible to the outer one that has to
    read it. The slot itself is passed by reference, so both sides see the same
    object no matter how many times the dict around it is copied.
    """

    def __init__(self) -> None:
        self.responder: Responder | None = None


class WriteLockMiddleware(BaseMiddleware):
    """Serializes request scopes and delivers their replies afterwards.

    Registered on ``dp.update.outer_middleware`` *before* ``setup_dishka``, and
    that ordering is the whole point: the unit of work commits when the dishka
    scope closes (see di.py ``RequestProvider.session``), which happens inside
    ``ContainerMiddleware``. A lock registered after it would be released before
    the commit it is supposed to cover, and the next update would read state the
    previous one had not yet written. Sitting outside also means the lock is
    already released when the replies go out — which is what keeps the network
    off the write path.

    SQLite allows exactly one writer. Without this lock, concurrency is
    arbitrated by ``busy_timeout`` (db/engine.py): the second writer blocks
    inside SQLite's busy handler and dies with "database is locked" once the
    wait runs out. That turns a queue into a deadline. Here the second writer
    waits on an asyncio lock instead: no timeout, no busy-handler spinning, and
    the event loop stays free to do the network I/O of updates that are already
    past their commit.

    ``BEGIN IMMEDIATE`` stays as-is. This lock covers writers inside the
    process, which D1's single-process bot makes sufficient in practice, but an
    alembic migration on startup or a hand-run script over the live database is
    still an outside writer.
    """

    def __init__(self, container: AsyncContainer) -> None:
        # Public: the background outbox sender takes the same lock. SQLite has
        # one writer per process, and that task is no exception.
        self.lock = asyncio.Lock()
        # The app-level container, not a request scope: the follow-up
        # transaction below opens its own session, after the update's scope has
        # already closed.
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        slot = _DeliverySlot()
        data[DELIVERY_SLOT] = slot
        async with self.lock:
            result = await handler(event, data)

        # The dishka scope has closed by now, so the transaction is committed
        # and the lock is gone. Only now do we touch the network.
        responder = slot.responder
        if responder is not None:
            # finally: a stale-message failure propagates to dp.errors, but the
            # rows that DID go out must still be struck off, or the background
            # sender would deliver them a second time.
            try:
                await responder.flush()
            finally:
                await self._settle(responder)
        return result

    async def _settle(self, responder: Responder) -> None:
        """Tie off what is only knowable after the send.

        A second, very short transaction — two millisecond-long ones instead of
        one that spanned a round trip to Telegram, which is exactly the trade
        this design is made of. Two things happen here:

        * delivered outbox rows are dropped (whatever is left, outbox.py keeps
          retrying);
        * the id of a pinned status message that had to be recreated is stored,
          because that id does not exist until the message has been sent.

        Losing this transaction is harmless either way: an undeleted outbox row
        means one duplicate reply, and a lost pinned id means the next sync
        creates another pinned message.
        """
        delivered = responder.delivered()
        if not delivered and not responder.pinned_updates:
            return
        factory = await self._container.get(async_sessionmaker[AsyncSession])
        async with self.lock, factory() as session:
            if delivered:
                await session.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(delivered)))
            users = UserService(session)
            for user_id, message_id in responder.pinned_updates:
                await users.set_pinned_message_id(user_id, message_id)
            await session.commit()
        delivered.clear()
        responder.pinned_updates.clear()


class ResponderMiddleware(BaseMiddleware):
    """Hands the request scope's Responder out, and records its promises.

    Registered on ``dp.update`` *after* ``setup_dishka``: the Responder lives in
    the dishka scope, but the flush has to happen once that scope has closed and
    committed — that is, in WriteLockMiddleware, which sits outside it and can no
    longer resolve anything from the container.

    The outbox rows are written here, on the way out, because this is the last
    point still inside the transaction. A handler that raised never gets here,
    which is the intended behaviour: no change, no promise to report one.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        container: AsyncContainer = data["dishka_container"]
        responder = await container.get(Responder)
        slot: _DeliverySlot | None = data.get(DELIVERY_SLOT)
        if slot is not None:
            slot.responder = responder

        result = await handler(event, data)
        await responder.persist(await container.get(AsyncSession))
        return result


# Telegram keeps unconfirmed updates for a day, so a week of marks is ample and
# keeps the table from growing without bound.
_MARK_TTL = timedelta(days=7)
_PRUNE_EVERY = timedelta(hours=1)


class IdempotencyMiddleware(BaseMiddleware):
    """Drops an update that has already been applied.

    Telegram confirms delivery only on the *next* getUpdates call, so a process
    killed right after committing gets the same update_id again. Deploys kill
    the bot on purpose, which means we aim at that window regularly.

    Registered on ``dp.update`` *after* ``setup_dishka`` so the request-scoped
    session already exists, and *before* the auth middleware so a redelivery
    never reaches ``ensure_registered`` either.
    """

    def __init__(self) -> None:
        self._pruned_at = utcnow() - _PRUNE_EVERY

    async def _prune(self, session: AsyncSession) -> None:
        now = utcnow()
        if now - self._pruned_at < _PRUNE_EVERY:
            return
        self._pruned_at = now
        await session.execute(
            delete(ProcessedUpdate).where(ProcessedUpdate.created_at < now - _MARK_TTL)
        )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_id = getattr(event, "update_id", None)
        if update_id is None:
            return await handler(event, data)

        container: AsyncContainer = data["dishka_container"]
        session = await container.get(AsyncSession)

        if await session.scalar(select(ProcessedUpdate.id).where(ProcessedUpdate.id == update_id)):
            logger.warning("Update %s already applied — redelivery dropped", update_id)
            return None

        # Added, never committed here. Committing separately would mark the
        # update as done *before* the change happened, and a crash in between
        # would turn the duplicate risk into a loss. RequestProvider commits it
        # together with the business change, or rolls both back on error.
        session.add(ProcessedUpdate(id=update_id))
        result = await handler(event, data)
        # Pruned only after the handler, never before it. A DELETE issued up
        # front takes SQLite's write lock for the whole request scope, and any
        # other session that needs to write during handling then dies with
        # "database is locked". Here the write lands right before the scope
        # commits, so the lock is held about as briefly as it always was.
        await self._prune(session)
        return result


class AuthMiddleware(BaseMiddleware):
    """Silently drops events from users who are neither admins nor allowlisted.

    A valid one-time ``/start <invite-code>`` deep link is the only way in for
    a stranger; everything else gets no response at all (see plan: no spam
    surface, no "request access" queue to moderate).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        container: AsyncContainer = data["dishka_container"]
        access = await container.get(AccessService)

        invite_attempted = False
        allowed = await access.is_allowed(user.id)
        if not allowed:
            invite_code = _invite_code_from_start(event)
            invite_attempted = invite_code is not None
            if invite_code and await access.redeem_invite(invite_code, user.id, user.username):
                allowed = True
                logger.info("Invite redeemed: user_id=%s username=%s", user.id, user.username)

        if not allowed:
            _auth_denials.log(
                logger,
                user.id,
                "Denied access: user_id=%s username=%s type=%s invite_attempted=%s",
                user.id,
                user.username,
                type(event).__name__,
                invite_attempted,
            )
            return None

        # Admins pass `is_allowed` without ever getting a `users` row (they're
        # recognized by config, not by the allowlist), so every other service
        # relying on that row existing needs it created here, unconditionally.
        await access.ensure_registered(user.id, user.username)
        return await handler(event, data)


def _invite_code_from_start(event: TelegramObject) -> str | None:
    if not isinstance(event, Message) or not event.text or not event.text.startswith("/start"):
        return None
    parts = event.text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else None
