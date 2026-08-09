from sqlalchemy.ext.asyncio import AsyncSession

from chain_health.bot import texts as bot_texts
from chain_health.db.models import Ride
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from tests.helpers.factories import onboard


async def _ride_rows(container) -> list:
    async with container() as rc:
        session = await rc.get(AsyncSession)
        return (await session.execute(Ride.__table__.select())).all()


async def test_numeric_message_records_a_ride_and_replies_with_the_cycle(harness):
    await onboard(harness)

    await harness.send("42", user_id=1)

    sent_texts = harness.session.sent_texts()
    assert any("42" in t for t in sent_texts)


async def test_zero_is_rejected_with_a_retry_message(harness, container):
    await onboard(harness)

    await harness.send("0", user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_DISTANCE_RETRY
    assert await _ride_rows(container) == []


async def test_five_digit_number_is_rejected_with_a_retry_message(harness, container):
    await onboard(harness)

    await harness.send("12345", user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_DISTANCE_RETRY
    assert await _ride_rows(container) == []


async def test_padded_number_is_accepted(harness, container):
    await onboard(harness)

    await harness.send(" 40 ", user_id=1)

    assert len(await _ride_rows(container)) == 1


async def test_comma_decimal_is_accepted(harness, container):
    await onboard(harness)

    await harness.send("1,5", user_id=1)

    assert len(await _ride_rows(container)) == 1


async def test_number_without_an_active_chain_replies_no_active_chain(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "admin")

    await harness.send("42", user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.NO_ACTIVE_CHAIN
    assert await _ride_rows(container) == []


async def test_over_limit_reply_carries_the_rotation_button(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "admin")
        garage = await rc.get(GarageService)
        group = await garage.create_group(1, "Шоссе", default_cycle_limit_km=10)
        await garage.add_chain(group, "A")
    harness.session.clear()

    await harness.send("42", user_id=1)

    sent = harness.session.calls_of("SendMessage")
    assert any(
        m.reply_markup is not None and hasattr(m.reply_markup, "inline_keyboard") for m in sent
    )


async def test_the_ride_is_committed_after_the_update(harness, container):
    await onboard(harness)

    await harness.send("42", user_id=1)

    assert len(await _ride_rows(container)) == 1
