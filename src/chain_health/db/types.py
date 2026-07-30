from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC instant.

    SQLite has no native timezone-aware timestamp type — SQLAlchemy's
    ``DateTime(timezone=True)`` silently drops the offset on this backend
    (verified empirically: both the bound value and the value read back lose
    ``tzinfo``). This decorator keeps every ``datetime`` that flows through
    the application timezone-aware (always UTC) while the underlying storage
    format is unchanged from a naive ``DateTime`` column — so swapping a
    column to this type needs no migration, only the 3 column renames in this
    change do.
    """

    impl = DateTime()
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{value!r} is naive; UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
