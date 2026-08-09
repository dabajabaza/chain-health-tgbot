from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetMe

from chain_health.bot.ui import Responder
from tests.helpers.bot_harness import RecordingSession
from tests.helpers.factories import (
    FAKE_BOT_TOKEN,
    make_group_with_chain,
    make_services,
    make_status_service,
    make_user,
    make_user_service,
)

_FAKE_METHOD = GetMe()


def _make_bot() -> tuple[Bot, RecordingSession]:
    bot = Bot(token=FAKE_BOT_TOKEN)
    session = RecordingSession()
    bot.session = session
    return bot, session


async def _deliver(responder: Responder, users) -> None:
    """Do what WriteLockMiddleware does once the transaction has committed:
    send what was recorded, then store the id of a pinned message that had to
    be recreated (it only exists after the send).
    """
    await responder.flush()
    for user_id, message_id in responder.pinned_updates:
        await users.set_pinned_message_id(user_id, message_id)
    responder.pinned_updates.clear()


async def _make_responder(session, settings, user_id: int) -> tuple[Responder, RecordingSession]:
    garage, rides = make_services(session, settings)
    status_service = make_status_service(garage, rides)
    users = make_user_service(session)
    await make_group_with_chain(garage, user_id)

    bot, recording = _make_bot()
    return Responder(bot, users, status_service), recording


async def test_first_sync_sends_and_pins_and_stores_the_id(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)

    assert len(recording.calls_of("SendMessage")) == 1
    assert len(recording.calls_of("PinChatMessage")) == 1
    assert await users.pinned_message_id(user.id) is not None


async def test_pin_failure_still_stores_the_message_id(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    recording.fail_on["PinChatMessage"] = TelegramAPIError(method=_FAKE_METHOD, message="boom")

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)

    assert await users.pinned_message_id(user.id) is not None


async def test_second_sync_edits_instead_of_sending(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)
    recording.clear()

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)

    assert recording.calls_of("SendMessage") == []
    assert len(recording.calls_of("EditMessageText")) == 1


async def test_message_not_modified_is_swallowed(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)
    recording.fail_on["EditMessageText"] = TelegramBadRequest(
        method=_FAKE_METHOD, message="Bad Request: message is not modified"
    )

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)  # must not raise


async def test_pinned_message_is_recreated_when_the_edit_fails(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)
    old_id = await users.pinned_message_id(user.id)

    recording.fail_on["EditMessageText"] = TelegramBadRequest(
        method=_FAKE_METHOD, message="Bad Request: message to edit not found"
    )
    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)

    new_id = await users.pinned_message_id(user.id)
    assert new_id != old_id
    assert len(recording.calls_of("PinChatMessage")) == 2


async def test_pinned_edit_failure_with_a_non_bad_request_error_is_swallowed(session, settings):
    """docs/ARCHITECTURE.md D10: the pinned sync is best-effort end to end,
    not just around pin_chat_message — a network error here must not
    propagate and roll back a transaction that already recorded a ride.
    """
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)
    recording.fail_on["EditMessageText"] = TelegramNetworkError(method=_FAKE_METHOD, message="boom")

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)  # must not raise


async def test_pinned_send_failure_on_first_sync_is_swallowed(session, settings):
    user = await make_user(session)
    responder, recording = await _make_responder(session, settings, user.id)
    users = make_user_service(session)
    recording.fail_on["SendMessage"] = TelegramNetworkError(method=_FAKE_METHOD, message="boom")

    await responder.sync_pinned(user.id, user.id)
    await _deliver(responder, users)  # must not raise
