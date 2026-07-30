import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from alembic import command
from alembic.config import Config as AlembicConfig
from dishka import AsyncContainer
from dishka.integrations.aiogram import setup_dishka

from chain_health.bot.errors import on_error
from chain_health.bot.handlers import admin, fallback, menu, mileage, rides, rotation, start, status
from chain_health.bot.middlewares import AuthMiddleware, PrivateChatOnlyMiddleware
from chain_health.config import Settings
from chain_health.di import build_container
from chain_health.scheduler import run_reminder_scheduler

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("chain_health")


def _run_migrations() -> None:
    cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


def build_dispatcher(container: AsyncContainer) -> Dispatcher:
    """Wires routers, the private-chat gate, dishka, and the auth middleware
    in the one order that matters — shared by production and the test harness
    so the two can never diverge (see docs/ARCHITECTURE.md D3).
    """
    dp = Dispatcher()
    dp.update.outer_middleware(PrivateChatOnlyMiddleware())

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(status.router)
    dp.include_router(menu.router)
    dp.include_router(rotation.router)
    dp.include_router(rides.router)
    dp.include_router(mileage.router)
    dp.include_router(fallback.router)  # must be last: catches any unmatched callback

    dp.errors.register(on_error)

    setup_dishka(container, dp, auto_inject=True)
    auth = AuthMiddleware()
    dp.message.outer_middleware(auth)
    dp.callback_query.outer_middleware(auth)

    return dp


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
    dp = build_dispatcher(container)

    await _set_commands(bot)

    scheduler_task = asyncio.create_task(run_reminder_scheduler(container, settings))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
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
