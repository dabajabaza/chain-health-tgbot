import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from aiogram import Bot
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from chain_health.__main__ import build_dispatcher
from chain_health.bot.handlers import (
    admin,
    chains,
    fallback,
    groups,
    menu,
    mileage,
    rides,
    rotation,
    start,
    status,
)
from chain_health.config import Settings
from chain_health.db.engine import create_engine
from chain_health.di import build_container
from tests.bot_harness import BotHarness, RecordingSession
from tests.factories import FAKE_BOT_TOKEN
from tests.schema import apply_migrations

# The handler modules define their routers as module-level singletons (correct
# for production: a real bot process builds its dispatcher exactly once). A
# Router can only ever attach to one parent Dispatcher for its lifetime, so
# rebuilding a Dispatcher per test — which build_dispatcher does, and must, to
# get a fresh dishka container per test — needs these detached in between.
_SHARED_ROUTERS = [
    admin.router,
    start.router,
    status.router,
    menu.router,
    groups.router,
    chains.router,
    rotation.router,
    rides.router,
    mileage.router,
    fallback.router,
]


def make_settings(db_path: Path, admin_ids: str = "1") -> Settings:
    return Settings(
        bot_token=FAKE_BOT_TOKEN,
        admin_ids_raw=admin_ids,
        db_path=db_path,
        _env_file=None,
    )


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Runs the real Alembic migration chain once per test session, so every
    test gets the production schema — not a `create_all` approximation that
    can silently drift from the migrations (this already happened once, see
    docs/ARCHITECTURE.md D16 / commit 3ca9e24).
    """
    path = tmp_path_factory.mktemp("schema") / "template.db"
    apply_migrations(f"sqlite:///{path}")
    return path


@pytest.fixture
def db_path(migrated_template: Path, tmp_path: Path) -> Path:
    """A private copy of the migrated template per test — cheap (a file copy)
    and avoids any cross-test state leakage a shared file would risk.
    """
    path = tmp_path / "test.db"
    shutil.copyfile(migrated_template, path)
    return path


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return make_settings(db_path)


@pytest_asyncio.fixture
async def engine(db_path: Path) -> AsyncIterator[AsyncEngine]:
    """Built with the production `db.engine.create_engine`, not a bare
    `create_async_engine` — the pragmas and, more importantly, the pysqlite
    transaction handling it configures are part of the semantics under test
    (a plain engine gets fake SAVEPOINTs; see test_engine.py).
    """
    eng = create_engine(f"sqlite+aiosqlite:///{db_path}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """A session that is never committed — each test runs inside one
    transaction that's simply abandoned at teardown. Fast, and exercising
    commit/rollback semantics is what test_di.py's dedicated tests are for.
    """
    async with session_factory() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def container(settings: Settings) -> AsyncIterator[AsyncContainer]:
    """A real dishka container wired against the same per-test SQLite file as
    the `session` fixture (a separate connection — SQLite handles concurrent
    readers/sequential writers on one file fine). Used by DI-graph, scheduler,
    and full-dispatcher handler tests, where a real commit/rollback boundary
    matters.
    """
    c = build_container(settings)
    yield c
    await c.close()


@pytest_asyncio.fixture
async def harness(container: AsyncContainer) -> AsyncIterator[BotHarness]:
    """A full Dispatcher (production wiring, via __main__.build_dispatcher)
    driven through fabricated updates with the network replaced by
    RecordingSession — for handler/middleware/error-handler integration
    tests.
    """
    bot = await container.get(Bot)
    session = RecordingSession()
    bot.session = session

    dp, write_lock = build_dispatcher(container)
    await dp.emit_startup()
    try:
        yield BotHarness(bot=bot, dp=dp, session=session, write_lock=write_lock)
    finally:
        await dp.emit_shutdown()
        for router in _SHARED_ROUTERS:
            router._parent_router = None  # noqa: SLF001
