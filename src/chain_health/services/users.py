from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.db.models import User


class UserService:
    """Per-user bot state (pinned status message).

    ``AccessService`` owns identity and the allowlist; this owns UI-adjacent
    state that would otherwise leak into the presentation layer (see
    docs/ARCHITECTURE.md D10, D12).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pinned_message_id(self, user_id: int) -> int | None:
        """Return the Telegram message id of the user's pinned status message, if stored."""
        user = await self._session.get(User, user_id)
        return None if user is None else user.pinned_message_id

    async def set_pinned_message_id(self, user_id: int, message_id: int) -> bool:
        """Store the pinned status message's id for the user.

        Returns False if the user row does not exist (never happens under
        AuthMiddleware, which registers every admitted user unconditionally).
        """
        user = await self._session.get(User, user_id)
        if user is None:
            return False
        user.pinned_message_id = message_id
        return True
