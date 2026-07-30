from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", populate_by_name=True
    )

    bot_token: str
    # Kept as raw strings and parsed via properties below: pydantic-settings tries to
    # JSON-decode env values for complex field types (set/time), which breaks on plain
    # values like "1" or "19:00".
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    db_path: Path = Path("data/chain_health.db")
    reminder_time_raw: str = Field(default="19:00", alias="REMINDER_TIME")
    tz: str = "UTC"

    @property
    def admin_ids(self) -> set[int]:
        try:
            return {int(chunk) for chunk in self.admin_ids_raw.split(",") if chunk.strip()}
        except ValueError as exc:
            raise ValueError(
                f"ADMIN_IDS must be comma-separated ints, got {self.admin_ids_raw!r}"
            ) from exc

    @property
    def reminder_time(self) -> time:
        try:
            hour, minute = self.reminder_time_raw.split(":")
            return time(int(hour), int(minute))
        except ValueError as exc:
            raise ValueError(
                f"REMINDER_TIME must be HH:MM, got {self.reminder_time_raw!r}"
            ) from exc

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @model_validator(mode="after")
    def _fail_fast_on_derived_values(self) -> "Settings":
        """Parse everything the properties above parse lazily, so a typo in
        .env kills startup instead of surfacing inside the 19:00 reminder run.
        """
        _ = self.admin_ids, self.reminder_time, self.timezone
        return self
