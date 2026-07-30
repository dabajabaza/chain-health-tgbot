from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from chain_health.timeutils import local_today, to_local_date, utcnow


def test_utcnow_is_timezone_aware():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.tzinfo == UTC


def test_to_local_date_same_day_no_offset():
    moment = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    assert to_local_date(moment, ZoneInfo("UTC")) == date(2026, 7, 30)


def test_to_local_date_crosses_day_boundary_east():
    # Late UTC evening is already the next day in Tokyo (UTC+9).
    moment = datetime(2026, 7, 30, 22, 0, tzinfo=UTC)
    assert to_local_date(moment, ZoneInfo("Asia/Tokyo")) == date(2026, 7, 31)


def test_to_local_date_crosses_day_boundary_west():
    # Early UTC morning is still the previous day in Los Angeles (UTC-7/8).
    moment = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
    assert to_local_date(moment, ZoneInfo("America/Los_Angeles")) == date(2026, 7, 29)


def test_local_today_matches_to_local_date_of_utcnow():
    tz = ZoneInfo("Europe/Berlin")
    assert local_today(tz) == to_local_date(utcnow(), tz)
