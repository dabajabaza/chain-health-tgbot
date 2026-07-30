from collections.abc import Sequence
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import ColumnElement

from chain_health.db.models import Chain, Ride, Rotation
from chain_health.domain.enums import RideSource
from chain_health.domain.errors import NotFoundError
from chain_health.domain.values import CycleBoundary


def _cycle_predicate(boundary: CycleBoundary) -> ColumnElement[bool]:
    """A ride is in the current cycle if it happened on a later local day than
    the rotation, or on the same local day but was recorded after it.

    ``ride_dt``/``activated_dt`` are local dates; ``created_at``/``activated_at``
    are UTC instants. The two halves never compare across clocks.
    """
    return or_(
        Ride.ride_dt > boundary.activated_dt,
        and_(
            Ride.ride_dt == boundary.activated_dt,
            Ride.created_at >= boundary.activated_at,
        ),
    )


class RideService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_ride(
        self,
        *,
        user_id: int,
        chain: Chain,
        distance_km: float,
        ride_dt: date,
        source: str = RideSource.MANUAL,
        external_id: str | None = None,
    ) -> Ride:
        ride = Ride(
            user_id=user_id,
            chain_id=chain.id,
            distance_km=distance_km,
            ride_dt=ride_dt,
            source=source,
            external_id=external_id,
        )
        self._session.add(ride)
        await self._session.flush()
        return ride

    async def require_ride(self, user_id: int, ride_id: int) -> Ride:
        """Raises NotFoundError if the ride does not exist or is not this user's."""
        stmt = select(Ride).where(Ride.id == ride_id, Ride.user_id == user_id)
        ride = await self._session.scalar(stmt)
        if ride is None:
            raise NotFoundError("ride", ride_id)
        return ride

    async def edit_ride(self, ride: Ride, distance_km: float) -> Ride:
        ride.distance_km = distance_km
        await self._session.flush()
        return ride

    async def delete_ride(self, ride: Ride) -> None:
        await self._session.delete(ride)
        await self._session.flush()

    async def recent_rides(self, user_id: int, limit: int = 10) -> Sequence[Ride]:
        stmt = (
            select(Ride)
            .where(Ride.user_id == user_id)
            .options(selectinload(Ride.chain))
            .order_by(Ride.created_at.desc())
            .limit(limit)
        )
        return (await self._session.scalars(stmt)).all()

    async def total_km(self, chain: Chain) -> float:
        stmt = select(func.coalesce(func.sum(Ride.distance_km), 0.0)).where(
            Ride.chain_id == chain.id
        )
        return await self._session.scalar(stmt) or 0.0

    async def cycle_km(self, chain: Chain) -> float:
        boundary = await self._last_activation(chain.id)
        stmt = select(func.coalesce(func.sum(Ride.distance_km), 0.0)).where(
            Ride.chain_id == chain.id
        )
        if boundary is not None:
            stmt = stmt.where(_cycle_predicate(boundary))
        return await self._session.scalar(stmt) or 0.0

    async def _last_activation(self, chain_id: int) -> CycleBoundary | None:
        stmt = (
            select(Rotation.activated_dt, Rotation.activated_at)
            .where(Rotation.chain_id == chain_id)
            .order_by(Rotation.activated_at.desc(), Rotation.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        return None if row is None else CycleBoundary(row.activated_dt, row.activated_at)
