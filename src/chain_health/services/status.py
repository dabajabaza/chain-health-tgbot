from chain_health.db.models import Chain, Group
from chain_health.domain.values import ChainStatus, StatusView
from chain_health.services.garage import GarageService
from chain_health.services.rides import RideService


class StatusService:
    """Assembles read-only mileage snapshots (ChainStatus / StatusView) for display."""

    def __init__(self, garage: GarageService, rides: RideService) -> None:
        self._garage = garage
        self._rides = rides

    async def build(self, user_id: int) -> StatusView:
        """Return the full status of the user's current group; empty view if they have none."""
        group = await self._garage.current_group(user_id)
        if group is None:
            return StatusView(group=None, active=None, all_chains=[])

        statuses = await self.build_for_group(group)
        active = next((s for s in statuses if s.chain.is_active), None)
        return StatusView(group=group, active=active, all_chains=statuses)

    async def build_for_group(self, group: Group) -> list[ChainStatus]:
        """Return a ChainStatus for every non-retired chain in the group, oldest first."""
        chains = await self._garage.list_chains(group)
        return [await self.chain_status(chain) for chain in chains]

    async def chain_status(self, chain: Chain) -> ChainStatus:
        """Return one chain's snapshot: current-cycle and lifetime mileage."""
        cycle_km = await self._rides.cycle_km(chain)
        total_km = await self._rides.total_km(chain)
        return ChainStatus(chain=chain, cycle_km=cycle_km, total_km=total_km)
