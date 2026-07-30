from zoneinfo import ZoneInfo

import pytest

from tests.factories import build_settings as _settings


def test_admin_ids_parses_comma_list():
    assert _settings(admin_ids_raw="1,2,3").admin_ids == {1, 2, 3}


def test_admin_ids_empty_string_is_empty_set():
    assert _settings(admin_ids_raw="").admin_ids == set()


def test_admin_ids_tolerates_spaces():
    assert _settings(admin_ids_raw=" 1 , 2 ").admin_ids == {1, 2}


def test_bad_admin_ids_fails_at_construction():
    with pytest.raises(ValueError, match="ADMIN_IDS"):
        _settings(admin_ids_raw="not-a-number")


def test_bad_reminder_time_fails_at_construction():
    with pytest.raises(ValueError, match="REMINDER_TIME"):
        _settings(reminder_time_raw="not-a-time")


def test_reminder_time_missing_colon_fails_at_construction():
    with pytest.raises(ValueError, match="REMINDER_TIME"):
        _settings(reminder_time_raw="19")


def test_unknown_timezone_fails_at_construction():
    with pytest.raises(Exception):  # noqa: B017 - ZoneInfo raises its own error type
        _settings(tz="Not/A_Real_Zone")


def test_reminder_time_parses_correctly():
    settings = _settings(reminder_time_raw="07:05")
    assert settings.reminder_time.hour == 7
    assert settings.reminder_time.minute == 5


def test_timezone_returns_zoneinfo():
    settings = _settings(tz="Europe/Berlin")
    assert settings.timezone == ZoneInfo("Europe/Berlin")


def test_database_url_uses_db_path():
    settings = _settings(db_path="data/wherever.db")
    assert settings.database_url == "sqlite+aiosqlite:///data/wherever.db"
