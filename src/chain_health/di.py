from collections.abc import AsyncIterable

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from chain_health.bot.notifier import TelegramReminderNotifier
from chain_health.bot.ui import Responder
from chain_health.config import Settings
from chain_health.db.engine import create_engine, create_session_factory
from chain_health.domain.ports import MileageSource, ReminderNotifier
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from chain_health.services.mileage import MileageService
from chain_health.services.reminders import ReminderService
from chain_health.services.rides import RideService
from chain_health.services.status import StatusService
from chain_health.services.users import UserService


class AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @provide
    def settings(self) -> Settings:
        return self._settings

    @provide
    async def bot(self, settings: Settings) -> AsyncIterable[Bot]:
        default = DefaultBotProperties(parse_mode=ParseMode.HTML)
        # Without TELEGRAM_PROXY aiogram builds its own session and connects
        # directly, which is what we want wherever Telegram is reachable.
        session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
        bot = Bot(token=settings.bot_token, default=default, session=session)
        yield bot
        await bot.session.close()

    @provide
    async def engine(self, settings: Settings) -> AsyncIterable[AsyncEngine]:
        """Disposed when the container closes, so the pool's connections go with it.

        A plain provider leaked them: the engine outlived every scope and its
        pooled aiosqlite connections were only ever reclaimed by the garbage
        collector, which complains about being handed an unclosed connection.
        """
        engine = create_engine(settings.database_url)
        yield engine
        await engine.dispose()

    @provide
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(engine)

    @provide
    def mileage_sources(self) -> dict[str, MileageSource]:
        # Stage 2: Strava/Garmin Connect/Komoot adapters register themselves here.
        return {}


class RequestProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def session(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterable[AsyncSession]:
        """Unit of work: one request scope = one transaction.

        dishka finalizes generator providers with ``agen.asend(exception)`` (see
        docs/ARCHITECTURE.md D11) — the exception arrives as the *value* of the
        ``yield`` expression, it is never thrown into the generator. A
        ``try/except`` wrapped around ``yield`` therefore never observes it, and
        ``commit()`` would run unconditionally even on a failed handler.
        """
        async with session_factory() as session:
            exception = yield session
            if exception is None:
                await session.commit()
            else:
                await session.rollback()

    access_service = provide(AccessService)
    ride_service = provide(RideService)
    garage_service = provide(GarageService)
    status_service = provide(StatusService)
    reminder_service = provide(ReminderService)
    user_service = provide(UserService)
    mileage_service = provide(MileageService)
    responder = provide(Responder)
    notifier = provide(TelegramReminderNotifier, provides=ReminderNotifier)


def build_container(settings: Settings, *overrides: Provider) -> AsyncContainer:
    return make_async_container(AppProvider(settings), RequestProvider(), *overrides)
