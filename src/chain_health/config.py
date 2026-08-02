from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from the environment / ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", populate_by_name=True
    )

    bot_token: str = Field(description="Telegram Bot API token issued by @BotFather")
    # Kept as raw strings and parsed via properties below: pydantic-settings tries to
    # JSON-decode env values for complex field types (set/time), which breaks on plain
    # values like "1" or "19:00".
    admin_ids_raw: str = Field(
        default="",
        alias="ADMIN_IDS",
        description="Comma-separated Telegram user ids with admin rights; parsed by `admin_ids`",
    )
    db_path: Path = Field(
        default=Path("data/chain_health.db"),
        description="Filesystem path to the SQLite database file",
    )
    reminder_time_raw: str = Field(
        default="19:00",
        alias="REMINDER_TIME",
        description="Local time of the daily reminder run as HH:MM; parsed by `reminder_time`",
    )
    tz: str = Field(
        default="UTC",
        description="IANA timezone name for 'local' dates and the reminder schedule",
    )
    telegram_proxy: str | None = Field(
        default=None,
        alias="TELEGRAM_PROXY",
        description=(
            "Proxy URL for reaching api.telegram.org, e.g. http://127.0.0.1:1080. "
            "Required where the ISP blocks Telegram; leave unset for a direct connection"
        ),
    )

    @property
    def admin_ids(self) -> set[int]:
        """The ADMIN_IDS env value parsed into a set of Telegram user ids."""
        try:
            return {int(chunk) for chunk in self.admin_ids_raw.split(",") if chunk.strip()}
        except ValueError as exc:
            raise ValueError(
                f"ADMIN_IDS must be comma-separated ints, got {self.admin_ids_raw!r}"
            ) from exc

    @property
    def reminder_time(self) -> time:
        """The REMINDER_TIME env value parsed into a local ``time``."""
        try:
            hour, minute = self.reminder_time_raw.split(":")
            return time(int(hour), int(minute))
        except ValueError as exc:
            raise ValueError(
                f"REMINDER_TIME must be HH:MM, got {self.reminder_time_raw!r}"
            ) from exc

    @property
    def timezone(self) -> ZoneInfo:
        """The ``tz`` setting resolved to a ``ZoneInfo`` instance."""
        return ZoneInfo(self.tz)

    @property
    def database_url(self) -> str:
        """SQLAlchemy async connection URL for the SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"

    @model_validator(mode="after")
    def _fail_fast_on_derived_values(self) -> "Settings":
        """Parse everything the properties above parse lazily, so a typo in
        .env kills startup instead of surfacing inside the 19:00 reminder run.
        """
        _ = self.admin_ids, self.reminder_time, self.timezone
        return self
