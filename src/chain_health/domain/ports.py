from datetime import date
from typing import Protocol

from chain_health.domain.values import ChainStatus, ExternalRide


class MileageSource(Protocol):
    """A pluggable origin of mileage data (Strava, Garmin Connect, Komoot, ...).

    Stage 2 concern: no adapters exist yet, only this port. Pull-only and
    stateless — the caller owns the cursor and passes it as ``since``, and the
    caller is responsible for dedup via ``(source, external_id)``. What is
    intentionally unsolved here (token storage, cursor persistence, idempotent
    insert, rate limits) is recorded in docs/ARCHITECTURE.md.
    """

    name: str

    async def fetch_rides(self, user_id: int, since: date | None = None) -> list[ExternalRide]: ...


class ReminderNotifier(Protocol):
    """Outbound port: how a due reminder reaches the user. Mirror of ``MileageSource``.

    Takes the domain ``ChainStatus`` — wording and transport belong to the
    adapter, not the caller.
    """

    async def send_reminder(self, user_id: int, status: ChainStatus) -> None: ...
