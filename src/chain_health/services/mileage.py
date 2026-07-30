from datetime import date

from chain_health.config import Settings
from chain_health.domain.enums import RideSource
from chain_health.domain.errors import NoActiveChainError
from chain_health.domain.values import RecordedRide
from chain_health.services.garage import GarageService
from chain_health.services.rides import RideService
from chain_health.services.status import StatusService
from chain_health.timeutils import local_today


class MileageService:
    """Records a ride against the user's current active chain.

    The single use-case entry point for "log a ride", shared by the manual
    text-entry handler and (in stage 2) any pull-based importer.
    """

    def __init__(
        self,
        garage: GarageService,
        rides: RideService,
        status_service: StatusService,
        settings: Settings,
    ) -> None:
        self._garage = garage
        self._rides = rides
        self._status_service = status_service
        self._settings = settings

    async def record_ride(
        self,
        *,
        user_id: int,
        distance_km: float,
        ride_dt: date | None = None,
        source: str = RideSource.MANUAL,
        external_id: str | None = None,
    ) -> RecordedRide:
        group = await self._garage.current_group(user_id)
        chain = await self._garage.active_chain(group) if group is not None else None
        if group is None or chain is None:
            raise NoActiveChainError

        ride = await self._rides.add_ride(
            user_id=user_id,
            chain=chain,
            distance_km=distance_km,
            ride_dt=ride_dt if ride_dt is not None else local_today(self._settings.timezone),
            source=source,
            external_id=external_id,
        )
        status = await self._status_service.chain_status(chain)
        return RecordedRide(group=group, ride=ride, status=status)
