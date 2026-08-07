from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from aiogram.types import TelegramObject
from dishka import AsyncContainer
from dishka.exceptions import ExitError
from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.bot.ui import Responder
from chain_health.db.models import Chain, User
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from chain_health.services.mileage import MileageService
from chain_health.services.reminders import ReminderService
from chain_health.services.rides import RideService
from chain_health.services.status import StatusService
from chain_health.services.users import UserService
from tests.bot_harness import BotHarness

REQUEST_SCOPED_SERVICES = [
    AsyncSession,
    AccessService,
    RideService,
    GarageService,
    StatusService,
    MileageService,
    ReminderService,
    UserService,
    Responder,
]


async def test_container_graph_resolves_every_request_scoped_dependency(
    container: AsyncContainer,
):
    """A broken provider wiring is otherwise only discovered at runtime, on
    whichever handler happens to touch it first — this catches it up front.
    """
    async with container() as rc:
        for cls in REQUEST_SCOPED_SERVICES:
            obj: object = await rc.get(cls)
            assert isinstance(obj, cls)


async def test_request_scope_commits_on_success(container: AsyncContainer):
    async with container() as rc:
        session = await rc.get(AsyncSession)
        session.add(User(id=555, username="ok"))
        await session.flush()

    async with container() as rc:
        session = await rc.get(AsyncSession)
        assert await session.get(User, 555) is not None


async def test_request_scope_rolls_back_on_exception(container: AsyncContainer):
    """Pins docs/ARCHITECTURE.md D11: dishka finalizes generator providers via
    ``agen.asend(exception)``, not by throwing into the generator. This test
    fails against the pre-fix di.py, where the exception was never observed
    and commit() ran unconditionally even for a failed scope.
    """
    with pytest.raises(RuntimeError):
        async with container() as rc:
            session = await rc.get(AsyncSession)
            session.add(User(id=556, username="should not persist"))
            await session.flush()
            raise RuntimeError("simulated handler failure")

    async with container() as rc:
        session = await rc.get(AsyncSession)
        assert await session.get(User, 556) is None


async def test_commit_time_constraint_violation_surfaces_as_exit_error(
    container: AsyncContainer,
):
    """A constraint violation caught only by SQLAlchemy's autoflush-at-commit
    (rather than an explicit mid-handler flush) is raised from inside the
    session provider's own finalization step, which dishka wraps in
    ExitError — the concrete shape of the docs/ARCHITECTURE.md D10 hazard
    (a commit failure surfacing after the handler already returned).
    """
    with pytest.raises(ExitError):
        async with container() as rc:
            session = await rc.get(AsyncSession)
            session.add(User(id=1))
            await session.flush()
            group_id = 1
            session.add_all(
                [
                    Chain(group_id=group_id, name="a", cycle_limit_km=1, is_active=True),
                    Chain(group_id=group_id, name="b", cycle_limit_km=1, is_active=True),
                ]
            )
            # No explicit flush: the partial-unique violation is only raised
            # when session.commit()'s implicit autoflush runs, inside the
            # provider's finalization — not here.


async def test_two_request_scopes_get_independent_sessions(container: AsyncContainer):
    async with container() as rc1, container() as rc2:
        session1 = await rc1.get(AsyncSession)
        session2 = await rc2.get(AsyncSession)
        assert session1 is not session2


async def test_one_update_runs_in_exactly_one_request_scope(harness: BotHarness):
    """One update = one unit of work, all the way through the middleware stack.

    ``setup_dishka`` opens a REQUEST scope on *every* aiogram observer, and
    aiogram nests observers, so before ``__main__._collapse_dishka_scopes`` a
    message entered one scope on ``update`` (where IdempotencyMiddleware runs)
    and a second, sibling scope on ``message`` (where auth and the handler
    run). Two scopes are two sessions on two connections: the
    ``processed_updates`` mark committed in a different transaction than the
    change it is supposed to be atomic with, and — once the engine opens real
    SQLite transactions — the outer scope's open transaction deadlocks the
    inner scope's writes.
    """
    seen: list[AsyncSession] = []

    async def capture(
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        seen.append(await data["dishka_container"].get(AsyncSession))
        return await handler(event, data)

    harness.dp.update.middleware(capture)
    harness.dp.message.outer_middleware(capture)

    await harness.send("/status", user_id=1)

    assert len(seen) == 2, "both the update-level and the message-level chain must run"
    assert seen[0] is seen[1]
