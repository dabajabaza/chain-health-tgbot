import secrets
from datetime import timedelta

from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.config import Settings
from chain_health.db.models import Invite, User
from chain_health.domain.constants import INVITE_CODE_BYTES, INVITE_TTL_HOURS
from chain_health.timeutils import utcnow


class AccessService:
    """Identity and admission: who is allowed in, and how invites are issued and redeemed.

    Admins come from config (``Settings.admin_ids``); everyone else must hold
    a ``users`` row, created either by an admin's ``/allow`` or by redeeming a
    one-time invite.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def is_admin(self, user_id: int) -> bool:
        """Return whether the user id is in the configured ADMIN_IDS set."""
        return user_id in self._settings.admin_ids

    async def is_allowed(self, user_id: int) -> bool:
        """Return whether the user may use the bot: an admin or an allowlisted user."""
        if self.is_admin(user_id):
            return True
        return await self._session.get(User, user_id) is not None

    async def ensure_registered(self, user_id: int, username: str | None) -> User:
        """Return the user's row, creating it (or refreshing the username) as needed.

        Idempotent, and safe against a concurrent update racing on the same
        user_id: aiogram runs updates as concurrent tasks (see D21), so
        two updates from a brand-new user's very first contact can both reach
        the ``user is None`` branch before either commits. A nested
        transaction (SAVEPOINT) around the insert means a UNIQUE-constraint
        loss here rolls back only the insert attempt, not the whole request,
        so the loser can just re-fetch the row the winner committed.
        """
        user = await self._session.get(User, user_id)
        if user is None:
            try:
                async with self._session.begin_nested():
                    user = User(id=user_id, username=username)
                    self._session.add(user)
                    await self._session.flush()
            except IntegrityError:
                user = await self._session.get(User, user_id)
                assert user is not None
        elif username is not None and user.username != username:
            user.username = username
        return user

    async def allow_user(self, user_id: int, username: str | None = None) -> User:
        """Admit a user to the allowlist by explicit admin action.

        The `/allow` admin command's entry point: same effect as
        ensure_registered, but this name is the one that should show up in an
        audit trail of "who did an admin explicitly let in" rather than
        "who got auto-registered as a side effect of some other action".
        """
        return await self.ensure_registered(user_id, username)

    async def create_invite(self, created_by: int) -> Invite:
        """Issue a one-time invite code that expires after INVITE_TTL_HOURS."""
        await self.ensure_registered(created_by, None)
        invite = Invite(
            code=secrets.token_urlsafe(INVITE_CODE_BYTES),
            created_by=created_by,
            expires_at=utcnow() + timedelta(hours=INVITE_TTL_HOURS),
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def redeem_invite(self, code: str, user_id: int, username: str | None) -> bool:
        """Claim the invite for this user; return False if unknown, spent, or expired.

        Atomic claim: a plain read-then-write here would let two
        concurrent ``/start <code>`` updates both observe ``used_by IS NULL``
        before either commits, and both redeem the same one-time invite. The
        UPDATE's WHERE clause is re-checked at write time, so only the first
        of two concurrent claims can match a row and get rowcount == 1;
        SQLite serializes writers, so there is no window between the check
        and the write for a second claim to slip through.
        """
        now = utcnow()
        stmt = (
            update(Invite)
            .where(Invite.code == code, Invite.used_by.is_(None), Invite.expires_at >= now)
            .values(used_by=user_id, used_at=now)
        )
        result = await self._session.execute(stmt)
        assert isinstance(result, CursorResult)
        if result.rowcount != 1:
            return False
        await self.ensure_registered(user_id, username)
        return True
