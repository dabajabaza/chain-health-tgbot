from pathlib import Path

from aiogram.fsm.storage.base import StorageKey

from chain_health.config import Settings
from chain_health.db.models import Chain, Group, User
from chain_health.domain.values import ChainStatus
from chain_health.services.garage import GarageService
from chain_health.services.mileage import MileageService
from chain_health.services.reminders import ReminderService
from chain_health.services.rides import RideService
from chain_health.services.status import StatusService
from chain_health.services.users import UserService
from tests.helpers.bot_harness import BotHarness

# Shared across every test that needs a syntactically valid but fake token —
# one literal, so a change to the format only needs updating here.
FAKE_BOT_TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def build_settings(
    *,
    admin_ids_raw: str = "1",
    reminder_time_raw: str = "19:00",
    tz: str = "UTC",
    db_path: Path | str = "data/chain_health.db",
) -> Settings:
    """A Settings instance that never touches a real .env file — for tests
    that only care about the parsed/derived values, not a real DB connection.
    """
    return Settings(
        bot_token=FAKE_BOT_TOKEN,
        admin_ids_raw=admin_ids_raw,
        reminder_time_raw=reminder_time_raw,
        tz=tz,
        db_path=Path(db_path),
        _env_file=None,
    )


class FakeNotifier:
    """Test double for ReminderNotifier: records calls instead of sending."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, ChainStatus]] = []
        self.raises: Exception | None = None

    async def send_reminder(self, user_id: int, status: ChainStatus) -> None:
        if self.raises is not None:
            raise self.raises
        self.sent.append((user_id, status))

    async def deliver_pending(self) -> None:
        """Nothing to deliver: send_reminder already recorded into `sent`."""


async def make_user(session, user_id: int = 1) -> User:
    user = User(id=user_id)
    session.add(user)
    await session.flush()
    return user


def make_services(session, settings: Settings) -> tuple[GarageService, RideService]:
    rides = RideService(session)
    garage = GarageService(session, rides, settings)
    return garage, rides


def make_status_service(garage: GarageService, rides: RideService) -> StatusService:
    return StatusService(garage, rides)


def make_mileage_service(
    garage: GarageService,
    rides: RideService,
    status_service: StatusService,
    settings: Settings,
) -> MileageService:
    return MileageService(garage, rides, status_service, settings)


def make_user_service(session) -> UserService:
    return UserService(session)


def make_reminder_service(
    session, status_service: StatusService, notifier: FakeNotifier | None = None
) -> tuple[ReminderService, FakeNotifier]:
    fake_notifier = notifier or FakeNotifier()
    return ReminderService(session, status_service, fake_notifier), fake_notifier


async def onboard(harness: BotHarness, user_id: int = 1) -> None:
    """Drives a fresh user through /start's onboarding wizard (creates the
    first group and chain), then clears the recording session so the test's
    own assertions start from a clean slate.
    """
    await harness.send("/start", user_id=user_id)
    await harness.send("Шоссе", user_id=user_id)
    await harness.send("Цепь A", user_id=user_id)
    harness.session.clear()


async def fsm_state(harness: BotHarness, user_id: int = 1) -> str | None:
    """The raw FSM state string currently stored for a user (None if no
    dialog is open) — private chats only (see D8), so chat_id == user_id.
    """
    key = StorageKey(bot_id=harness.bot.id, chat_id=user_id, user_id=user_id)
    return await harness.dp.fsm.storage.get_state(key)


async def make_group_with_chain(
    garage: GarageService,
    user_id: int,
    group_name: str = "Шоссе",
    chain_name: str = "Цепь А",
    default_cycle_limit_km: float = 300.0,
) -> tuple[Group, Chain]:
    group = await garage.create_group(user_id, group_name, default_cycle_limit_km)
    chain = await garage.add_chain(group, chain_name)
    return group, chain
