from chain_health.bot import texts as bot_texts
from chain_health.services.garage import GarageService


async def test_a_very_long_name_during_onboarding_is_rejected_with_a_retry_message(
    harness, container
):
    """Same MAX_NAME_LENGTH check as menu.py's fsm_add_group_name — without
    it, an oversized name during onboarding leaves the user with no group at
    all after group_detail_text/pin sync fails against Telegram's message
    length limit (see docs findings on unbounded name length).
    """
    long_name = "A" * 200

    await harness.send("/start", user_id=1)
    await harness.send(long_name, user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_NAME_TOO_LONG_RETRY

    await harness.send("Шоссе", user_id=1)
    await harness.send(long_name, user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_NAME_TOO_LONG_RETRY
    async with container() as rc:
        garage = await rc.get(GarageService)
        assert await garage.list_groups(1) == []
