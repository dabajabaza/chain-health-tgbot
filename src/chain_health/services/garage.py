from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.config import Settings
from chain_health.db.models import Chain, Group, Rotation, User
from chain_health.domain.constants import DEFAULT_CYCLE_LIMIT_KM
from chain_health.domain.errors import InvalidOperationError, NotFoundError
from chain_health.domain.values import RotationOptions
from chain_health.services.rides import RideService
from chain_health.timeutils import to_local_date, utcnow


class GarageService:
    """Manages the user's garage: groups (bikes), chains, and rotations.

    Owns the "one active chain per group" invariant — every activation and
    deactivation goes through this service.
    """

    def __init__(self, session: AsyncSession, rides: RideService, settings: Settings) -> None:
        self._session = session
        self._rides = rides
        self._settings = settings

    async def create_group(
        self,
        user_id: int,
        name: str,
        default_cycle_limit_km: float = DEFAULT_CYCLE_LIMIT_KM,
    ) -> Group:
        """Create a group and make it the user's current one if they had none."""
        group = Group(user_id=user_id, name=name, default_cycle_limit_km=default_cycle_limit_km)
        self._session.add(group)
        await self._session.flush()
        user = await self._session.get(User, user_id)
        if user is not None and user.current_group_id is None:
            user.current_group_id = group.id
        return group

    async def list_groups(self, user_id: int) -> Sequence[Group]:
        """Return all of the user's groups, oldest first."""
        stmt = select(Group).where(Group.user_id == user_id).order_by(Group.created_at)
        return (await self._session.scalars(stmt)).all()

    async def require_group(self, user_id: int, group_id: int) -> Group:
        """Return the group, raising NotFoundError if it does not exist or is not this user's.

        The only source of a Group for an untrusted (e.g. callback-supplied) id.
        """
        stmt = select(Group).where(Group.id == group_id, Group.user_id == user_id)
        group = await self._session.scalar(stmt)
        if group is None:
            raise NotFoundError("group", group_id)
        return group

    async def require_chain(self, user_id: int, chain_id: int) -> Chain:
        """Return the chain, raising NotFoundError if missing or its group is not this user's."""
        stmt = (
            select(Chain)
            .join(Group, Chain.group_id == Group.id)
            .where(Chain.id == chain_id, Group.user_id == user_id)
        )
        chain = await self._session.scalar(stmt)
        if chain is None:
            raise NotFoundError("chain", chain_id)
        return chain

    async def current_group(self, user_id: int) -> Group | None:
        """Return the user's current group, or None if unset or no longer theirs."""
        user = await self._session.get(User, user_id)
        if user is None or user.current_group_id is None:
            return None
        stmt = select(Group).where(Group.id == user.current_group_id, Group.user_id == user_id)
        return await self._session.scalar(stmt)

    async def current_active_chain(self, user_id: int) -> Chain | None:
        """Return the active chain of the user's current group, or None at either step."""
        group = await self.current_group(user_id)
        if group is None:
            return None
        return await self._active_chain(group.id)

    async def set_current_group(self, user_id: int, group: Group) -> None:
        """Make ``group`` the user's current one; raises NotFoundError for an unknown user."""
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError("user", user_id)
        user.current_group_id = group.id

    async def add_chain(self, group: Group, name: str, *, activate: bool = False) -> Chain:
        """Add a chain with the group's default limit; activate it if asked or if first."""
        chain = Chain(group_id=group.id, name=name, cycle_limit_km=group.default_cycle_limit_km)
        self._session.add(chain)
        await self._session.flush()

        existing_active = await self._active_chain(group.id)
        if activate or existing_active is None:
            if existing_active is not None and existing_active.id != chain.id:
                existing_active.is_active = False
                # Flush the deactivation on its own: SQLAlchemy batches same-table
                # UPDATEs by primary key, not by the order attributes were set in
                # Python, so a single flush covering both this row and the
                # about-to-be-activated one can transiently violate the
                # partial-unique "one active chain per group" index if the new
                # chain's id happens to sort before the old one's.
                await self._session.flush()
            await self._activate(chain)
        return chain

    async def list_chains(self, group: Group, *, include_retired: bool = False) -> Sequence[Chain]:
        """Return the group's chains, oldest first, hiding retired ones by default."""
        stmt = select(Chain).where(Chain.group_id == group.id)
        if not include_retired:
            stmt = stmt.where(Chain.is_retired.is_(False))
        stmt = stmt.order_by(Chain.created_at)
        return (await self._session.scalars(stmt)).all()

    async def active_chain(self, group: Group) -> Chain | None:
        """Return the group's active chain, or None if it has none."""
        return await self._active_chain(group.id)

    async def rotation_options(self, group: Group) -> RotationOptions:
        """Return the non-active, non-retired chains with their lifetime mileage.

        The recommendation is the lowest lifetime total; ties go to whichever
        chain sorts first from ``list_chains`` (oldest, since it orders by
        ``created_at``) thanks to ``min``'s stable tie-break.
        """
        candidates = [c for c in await self.list_chains(group) if not c.is_active]
        totals = {c.id: await self._rides.total_km(c) for c in candidates}
        recommended = min(candidates, key=lambda c: totals[c.id], default=None)
        return RotationOptions(candidates=candidates, totals=totals, recommended=recommended)

    async def rotate(self, chain: Chain) -> Chain:
        """Make ``chain`` the group's active one, starting a fresh cycle for it.

        Raises InvalidOperationError for a retired or already-active chain.
        """
        if chain.is_retired:
            raise InvalidOperationError(f"Chain {chain.id} is retired")
        if chain.is_active:
            # Guards against a stale/duplicate RotateCB confirm click (rotation
            # prompts live forever in chat history): without this, re-activating
            # an already-active chain would insert a fresh Rotation and silently
            # zero its accumulated cycle_km.
            raise InvalidOperationError(f"Chain {chain.id} is already active")

        current_active = await self._active_chain(chain.group_id)
        if current_active is not None and current_active.id != chain.id:
            current_active.is_active = False
            # See the matching comment in add_chain: this flush must happen
            # before _activate(), not be batched together with it.
            await self._session.flush()
        await self._activate(chain)
        return chain

    async def retire_chain(self, chain: Chain) -> Chain:
        """Permanently take the chain out of rotation, deactivating it if active."""
        chain.is_active = False
        chain.is_retired = True
        chain.retired_at = utcnow()
        await self._session.flush()
        return chain

    async def set_group_default_limit(self, group: Group, limit_km: float) -> None:
        """Set the cycle limit that chains added to this group from now on inherit."""
        group.default_cycle_limit_km = limit_km

    async def set_chain_limit(self, chain: Chain, limit_km: float) -> None:
        """Set this chain's own per-cycle mileage limit."""
        chain.cycle_limit_km = limit_km

    async def set_chain_resource(self, chain: Chain, resource_km: float | None) -> None:
        """Set the chain's total lifetime resource; None disables the wear warning."""
        chain.resource_km = resource_km

    async def _active_chain(self, group_id: int) -> Chain | None:
        stmt = select(Chain).where(Chain.group_id == group_id, Chain.is_active.is_(True))
        return await self._session.scalar(stmt)

    async def _activate(self, chain: Chain) -> None:
        activated_at = utcnow()
        chain.is_active = True
        self._session.add(
            Rotation(
                group_id=chain.group_id,
                chain_id=chain.id,
                activated_at=activated_at,
                activated_dt=to_local_date(activated_at, self._settings.timezone),
            )
        )
        await self._session.flush()
