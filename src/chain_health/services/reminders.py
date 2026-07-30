from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.db.models import User
from chain_health.domain.ports import ReminderNotifier
from chain_health.domain.values import DueReminder
from chain_health.services.status import StatusService


class ReminderService:
    """Decides who is due a daily over-limit/wear reminder and records that it was sent.

    Timing lives in scheduler.py; message wording and transport live behind
    the ``ReminderNotifier`` port.
    """

    def __init__(
        self, session: AsyncSession, status_service: StatusService, notifier: ReminderNotifier
    ) -> None:
        self._session = session
        self._status_service = status_service
        self._notifier = notifier

    async def due_user_ids(self, today_dt: date) -> list[int]:
        """Return the ids of every user due a reminder today.

        Builds the full DueReminder list (see D6 on the per-chain query
        cost) just to project out the id. scheduler.py's phase 2 then
        recomputes the same status per user anyway, in its own fresh scope —
        deliberately, so a stale phase-1 status can't be notified against
        (the user may have ridden or rotated in between). Not the same
        duplication D6 sanctions; documented here since it's the reason
        this can't just cache and reuse phase 1's ChainStatus.
        """
        return [reminder.user_id for reminder in await self.due_reminders(today_dt)]

    async def due_reminders(
        self, today_dt: date, *, user_id: int | None = None
    ) -> list[DueReminder]:
        """Return the reminders due today: active chain over limit or past its resource.

        Users already reminded today are skipped; ``user_id`` narrows the
        check to one user (the phase-2 re-check in scheduler.py).
        """
        stmt = select(User).where(User.current_group_id.is_not(None))
        if user_id is not None:
            stmt = stmt.where(User.id == user_id)
        users = (await self._session.scalars(stmt)).all()

        due = []
        for user in users:
            if user.last_reminder_sent_dt is not None and user.last_reminder_sent_dt >= today_dt:
                continue
            view = await self._status_service.build(user.id)
            if view.active is None:
                continue
            if view.active.over_limit or view.active.resource_warning:
                due.append(DueReminder(user_id=user.id, status=view.active))
        return due

    async def send_due_reminder(self, user_id: int, today_dt: date) -> bool:
        """Re-check this user (inside the caller's transaction) and, if still
        due, notify then mark. Returns False if no longer due — they already
        rode, rotated, or were reminded by an earlier run.

        Notify-then-mark, not mark-then-notify: a failed send rolls back the
        mark too, so the reminder is retried on the next run instead of being
        silently dropped for the day.
        """
        reminders = await self.due_reminders(today_dt, user_id=user_id)
        if not reminders:
            return False
        reminder = reminders[0]
        await self._notifier.send_reminder(reminder.user_id, reminder.status)
        await self.mark_reminded(user_id, today_dt)
        return True

    async def mark_reminded(self, user_id: int, today_dt: date) -> None:
        """Record that the user was reminded today, suppressing repeats until tomorrow."""
        user = await self._session.get(User, user_id)
        if user is not None:
            user.last_reminder_sent_dt = today_dt
