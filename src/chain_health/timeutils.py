from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Timezone-aware current instant in UTC."""
    return datetime.now(UTC)


def to_local_date(moment: datetime, tz: ZoneInfo) -> date:
    """Local calendar date of a timezone-aware instant."""
    return moment.astimezone(tz).date()


def local_today(tz: ZoneInfo) -> date:
    return to_local_date(utcnow(), tz)


def local_now(tz: ZoneInfo) -> datetime:
    """Current instant, converted to the local wall clock.

    Routes through ``utcnow()`` like every other "now" in the app (see
    docs/ARCHITECTURE.md D9) — nothing calls ``datetime.now(tz)`` directly,
    so there is exactly one clock read to fake in tests.
    """
    return utcnow().astimezone(tz)
