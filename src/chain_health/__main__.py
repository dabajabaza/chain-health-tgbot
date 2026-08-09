import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from alembic import command
from alembic.config import Config as AlembicConfig
from dishka import AsyncContainer
from dishka.integrations.aiogram import ContainerMiddleware, setup_dishka
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from chain_health.bot.errors import on_error
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
from chain_health.bot.middlewares import (
    AuthMiddleware,
    IdempotencyMiddleware,
    PrivateChatOnlyMiddleware,
    ResponderMiddleware,
    WriteLockMiddleware,
)
from chain_health.config import Settings
from chain_health.di import build_container
from chain_health.runtime.outbox import run_sender
from chain_health.runtime.scheduler import run_reminder_scheduler
from chain_health.runtime.watchdog import run_watchdog, sd_notify

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Liveness ping cadence. The supervisor's own timeout has to be comfortably
# larger than this interval, or a single slow probe would look like a hang;
# the server pairs these 30s with a 90s timeout.
_WATCHDOG_INTERVAL = 30
_WATCHDOG_PROBE_TIMEOUT = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("chain_health")


def _run_migrations() -> None:
    cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


# dishka's setup_dishka() opens a REQUEST scope on *every* aiogram observer.
# aiogram nests observers — an incoming message runs the `update` chain, which
# then propagates into the `message` chain — so out of the box a single update
# entered two sibling REQUEST scopes and got two AsyncSessions on two
# connections: one for IdempotencyMiddleware (registered on `update`), another
# for AuthMiddleware and the handler (`message`/`callback_query`). That breaks
# the unit of work both di.py and IdempotencyMiddleware document: the
# processed_updates mark committed in a *different* transaction than the
# business change it is supposed to be atomic with, so a crash in the window
# between the two commits still replayed the update. It is also a deadlock
# against real SQLite transactions (see db/engine.py): the outer scope holds a
# transaction open across the inner scope's writes.
#
# The `error` observer keeps its own scope on purpose: aiogram propagates an
# error only after the update chain's outer middlewares have unwound, so the
# scope that raised is already closed by the time on_error runs.
_SCOPE_OWNING_OBSERVERS = frozenset({"update", "error"})


def _collapse_dishka_scopes(dp: Dispatcher) -> None:
    """Leave exactly one dishka REQUEST scope per update."""
    for name, observer in dp.observers.items():
        if name in _SCOPE_OWNING_OBSERVERS:
            continue
        for middleware in list(observer.outer_middleware):
            if isinstance(middleware, ContainerMiddleware):
                observer.outer_middleware.unregister(middleware)


def build_dispatcher(container: AsyncContainer) -> tuple[Dispatcher, asyncio.Lock]:
    """Wires routers, the private-chat gate, dishka, and the auth middleware
    in the one order that matters — shared by production and the test harness
    so the two can never diverge (see docs/ARCHITECTURE.md D3).
    """
    dp = Dispatcher()
    dp.update.outer_middleware(PrivateChatOnlyMiddleware())
    # After the private-chat gate (group traffic has no business queueing for a
    # write lock) and before setup_dishka: the unit of work commits as the
    # dishka scope closes, so the lock has to be *outside* ContainerMiddleware
    # to still be held at that moment — and released before the replies go out.
    # See WriteLockMiddleware.
    write_lock_mw = WriteLockMiddleware(container)
    dp.update.outer_middleware(write_lock_mw)
    # The outbox sender needs the same lock. Returned to the caller rather than
    # stashed under a magic dp["…"] key: a typo in a string key (or a future
    # builder that forgets to set it) silently splits the single writer into
    # two locks — the loser dies "database is locked" after busy_timeout.
    dp["write_lock"] = write_lock_mw.lock  # kept for introspection only

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(status.router)
    dp.include_router(menu.router)
    # groups/chains before mileage: their fsm_* handlers take bare numbers under
    # a state, and mileage.router catches any bare number via NUMERIC_MESSAGE_RE.
    dp.include_router(groups.router)
    dp.include_router(chains.router)
    dp.include_router(rotation.router)
    dp.include_router(rides.router)
    dp.include_router(mileage.router)
    dp.include_router(fallback.router)  # must be last: catches any unmatched callback

    dp.errors.register(on_error)

    setup_dishka(container, dp, auto_inject=True)
    _collapse_dishka_scopes(dp)
    # After dishka (needs the request-scoped session) and before auth: a
    # redelivered update must not reach ensure_registered either.
    dp.update.middleware(ResponderMiddleware())
    dp.update.middleware(IdempotencyMiddleware())
    auth = AuthMiddleware()
    dp.message.outer_middleware(auth)
    dp.callback_query.outer_middleware(auth)

    return dp, write_lock_mw.lock


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="status", description="Показать статус цепей"),
            BotCommand(command="menu", description="Открыть меню"),
        ]
    )


async def _run_bot(settings: Settings) -> None:
    container = build_container(settings)
    bot = await container.get(Bot)
    dp, write_lock = build_dispatcher(container)

    await _set_commands(bot)

    # Reaching this point means the bot talked to Telegram successfully, so it
    # is the honest moment to report readiness.
    sd_notify("READY=1")

    scheduler_task = asyncio.create_task(run_reminder_scheduler(container, settings))
    watchdog_task = asyncio.create_task(
        run_watchdog(bot, interval=_WATCHDOG_INTERVAL, probe_timeout=_WATCHDOG_PROBE_TIMEOUT)
    )
    # Finishes off replies whose delivery never happened — usually because the
    # process died between the commit and the send, which a deploy does on purpose.
    session_factory = await container.get(async_sessionmaker[AsyncSession])
    engine = await container.get(AsyncEngine)
    outbox_task = asyncio.create_task(run_sender(bot, session_factory, engine, write_lock))
    try:
        await dp.start_polling(bot)
    finally:
        for task in (scheduler_task, watchdog_task, outbox_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await container.close()


def main() -> None:
    settings = Settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Applying database migrations")
    _run_migrations()
    logger.info("Starting bot")
    asyncio.run(_run_bot(settings))


if __name__ == "__main__":
    main()
