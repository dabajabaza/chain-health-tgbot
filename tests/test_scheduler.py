from datetime import datetime

from aiogram import Bot

import chain_health.scheduler as scheduler_module
import chain_health.timeutils as timeutils_module
from chain_health.scheduler import (
    is_past_reminder_time,
    seconds_until_next_run,
    send_due_reminders,
)
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from chain_health.services.rides import RideService
from chain_health.timeutils import local_today
from tests.bot_harness import RecordingSession
from tests.factories import build_settings as _settings


def _freeze_at(monkeypatch, instant: datetime) -> None:
    """Fakes the single clock read every timing function in scheduler.py
    goes through (timeutils.utcnow, per docs/ARCHITECTURE.md D9/D14) —
    no Clock-DI abstraction needed in production code for this.
    """
    monkeypatch.setattr(timeutils_module, "utcnow", lambda: instant)


def test_seconds_until_next_run_before_target_today(monkeypatch):
    settings = _settings(reminder_time_raw="19:00", tz="UTC")
    _freeze_at(monkeypatch, datetime(2026, 7, 30, 10, 0, tzinfo=settings.timezone))

    seconds = seconds_until_next_run(settings)

    assert seconds == 9 * 3600


def test_seconds_until_next_run_after_target_rolls_to_tomorrow(monkeypatch):
    settings = _settings(reminder_time_raw="19:00", tz="UTC")
    _freeze_at(monkeypatch, datetime(2026, 7, 30, 20, 0, tzinfo=settings.timezone))

    seconds = seconds_until_next_run(settings)

    assert seconds == 23 * 3600


def test_seconds_until_next_run_at_the_exact_target_rolls_to_tomorrow(monkeypatch):
    settings = _settings(reminder_time_raw="19:00", tz="UTC")
    _freeze_at(monkeypatch, datetime(2026, 7, 30, 19, 0, tzinfo=settings.timezone))

    seconds = seconds_until_next_run(settings)

    assert seconds == 24 * 3600


def test_seconds_until_next_run_survives_a_dst_transition_day(monkeypatch):
    # Europe/Berlin springs forward on the last Sunday of March.
    settings = _settings(reminder_time_raw="19:00", tz="Europe/Berlin")
    _freeze_at(monkeypatch, datetime(2026, 3, 29, 1, 0, tzinfo=settings.timezone))

    seconds = seconds_until_next_run(settings)

    assert seconds > 0


def test_is_past_reminder_time_true_after_and_false_before(monkeypatch):
    settings = _settings(reminder_time_raw="19:00", tz="UTC")

    _freeze_at(monkeypatch, datetime(2026, 7, 30, 10, 0, tzinfo=settings.timezone))
    assert is_past_reminder_time(settings) is False

    _freeze_at(monkeypatch, datetime(2026, 7, 30, 20, 0, tzinfo=settings.timezone))
    assert is_past_reminder_time(settings) is True


def test_scheduler_module_does_not_import_the_presentation_layer():
    """Fitness test pinning docs/ARCHITECTURE.md D17: scheduler.py must stay a
    pure-timing module. If this ever fails, ReminderService/ReminderNotifier
    stopped doing their job of keeping bot/texts out of the scheduler.
    """
    module_globals = vars(scheduler_module)
    assert "texts" not in module_globals
    assert "Responder" not in module_globals


async def test_send_due_reminders_opens_a_fresh_scope_per_user(container, settings):
    bot = await container.get(Bot)
    bot.session = RecordingSession()

    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "a")
        await access.ensure_registered(2, "b")

        garage = await rc.get(GarageService)
        rides = await rc.get(RideService)
        today = local_today(settings.timezone)
        for uid in (1, 2):
            group = await garage.create_group(uid, "G", default_cycle_limit_km=1)
            chain = await garage.add_chain(group, "C")
            await rides.add_ride(user_id=uid, chain=chain, distance_km=10, ride_dt=today)

    sent = await send_due_reminders(container, settings)

    assert sent == 2


async def test_send_due_reminders_is_idempotent_within_a_day(container, settings):
    bot = await container.get(Bot)
    bot.session = RecordingSession()

    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "a")
        garage = await rc.get(GarageService)
        rides = await rc.get(RideService)
        today = local_today(settings.timezone)
        group = await garage.create_group(1, "G", default_cycle_limit_km=1)
        chain = await garage.add_chain(group, "C")
        await rides.add_ride(user_id=1, chain=chain, distance_km=10, ride_dt=today)

    first = await send_due_reminders(container, settings)
    second = await send_due_reminders(container, settings)

    assert first == 1
    assert second == 0
