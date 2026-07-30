import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from chain_health.services.access import AccessService

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
