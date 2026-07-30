from dataclasses import dataclass
from datetime import date, datetime

from chain_health.db.models import Chain, Group, Ride


@dataclass(frozen=True, slots=True)
class ChainStatus:
    """One chain's mileage snapshot: current-cycle and lifetime totals."""

    chain: Chain
    cycle_km: float
    total_km: float

    @property
    def cycle_limit_km(self) -> float:
        """Re-exposed from ``chain`` so bot/texts.py can read cycle_km,
        total_km, and cycle_limit_km through one uniform ``status.*``
        interface, instead of some fields coming from ChainStatus and this
        one alone reaching into ``status.chain``.
        """
        return self.chain.cycle_limit_km

    @property
    def over_limit(self) -> bool:
        """Whether the current cycle has reached the chain's rotation limit."""
        return self.cycle_km >= self.cycle_limit_km

    @property
    def resource_warning(self) -> bool:
        """Whether lifetime mileage has reached the chain's total resource, if one is set."""
        return self.chain.resource_km is not None and self.total_km >= self.chain.resource_km


@dataclass(frozen=True, slots=True)
class StatusView:
    """Everything the status screen shows for one user's current group.

    ``group`` and ``active`` are None when the user has no current group or
    it has no active chain, respectively.
    """

    group: Group | None
    active: ChainStatus | None
    all_chains: list[ChainStatus]


@dataclass(frozen=True, slots=True)
class RotationOptions:
    """Chains eligible for rotation, keyed by lifetime mileage (``totals`` maps chain id to km).

    ``recommended`` is the least-worn candidate, or None when there are no
    candidates at all.
    """

    candidates: list[Chain]
    totals: dict[int, float]
    recommended: Chain | None


@dataclass(frozen=True, slots=True)
class CycleBoundary:
    """Start of the current cycle for a chain: local date + UTC instant of activation.

    ``activated_dt`` shares its "clock" with ``Ride.ride_dt`` (local calendar
    date); ``activated_at`` shares its clock with ``Ride.created_at`` (UTC
    instant). The cycle predicate must never compare across these two.
    """

    activated_dt: date
    activated_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedRide:
    """Result of logging a ride: the ride itself plus the chain's status right after it."""

    group: Group
    ride: Ride
    status: ChainStatus


@dataclass(frozen=True, slots=True)
class DueReminder:
    """A reminder that should be sent: the user and the chain status that triggered it."""

    user_id: int
    status: ChainStatus


@dataclass(frozen=True, slots=True)
class ExternalRide:
    """A ride as reported by a MileageSource, before it is persisted as a Ride."""

    external_id: str
    ride_dt: date
    distance_km: float
