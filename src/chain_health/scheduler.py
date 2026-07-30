import asyncio
import logging
from datetime import timedelta

from dishka import AsyncContainer

from chain_health.config import Settings
from chain_health.services.reminders import ReminderService
from chain_health.timeutils import local_now, local_today

logger = logging.getLogger("chain_health.scheduler")


async def run_reminder_scheduler(container: AsyncContainer, settings: Settings) -> None:
    """Timing only. What gets sent and how lives in ReminderService +
    ReminderNotifier — this module must stay free of bot/texts imports (see
    docs/ARCHITECTURE.md D17/fitness test in test_scheduler.py).
    """
    if is_past_reminder_time(settings):
        await _run_safely(container, settings)
    while True:
        await asyncio.sleep(seconds_until_next_run(settings))
        await _run_safely(container, settings)


def seconds_until_next_run(settings: Settings) -> float:
    now = local_now(settings.timezone)
    target = now.replace(
        hour=settings.reminder_time.hour,
        minute=settings.reminder_time.minute,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def is_past_reminder_time(settings: Settings) -> bool:
    return local_now(settings.timezone).time() >= settings.reminder_time


async def _run_safely(container: AsyncContainer, settings: Settings) -> None:
    try:
        sent = await send_due_reminders(container, settings)
        if sent:
            logger.info("Sent %d reminder(s)", sent)
    except Exception:
        logger.exception("Reminder run failed")


async def send_due_reminders(container: AsyncContainer, settings: Settings) -> int:
    """Phase 1 collects the due set in one scope. Phase 2 opens a FRESH
    request scope per user, so one user's failure rolls back only their own
    mark_reminded — not the whole batch.
    """
    today = local_today(settings.timezone)
    async with container() as scope:
        user_ids = await (await scope.get(ReminderService)).due_user_ids(today)

    sent = 0
    for user_id in user_ids:
        try:
            async with container() as scope:
                reminders = await scope.get(ReminderService)
                if await reminders.send_due_reminder(user_id, today):
                    sent += 1
        except Exception:
            logger.exception("Failed to send a reminder to user %s", user_id)
    return sent
