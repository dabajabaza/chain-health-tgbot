import asyncio

from chain_health.bot import texts as bot_texts
from chain_health.services.access import AccessService
from tests.bot_harness import make_update_message


async def test_two_concurrent_invite_redemptions_only_one_succeeds(harness, container):
    """Reproduces the review's redeem_invite race: two /start <code> updates
    for the same one-time invite, fed through the real dispatcher
    concurrently (aiogram processes updates as concurrent tasks — each gets
    its own dishka request scope and its own transaction). Before the fix
    (non-atomic read-then-write on invites.used_by), both could observe
    used_by IS NULL before either committed and both would be admitted.
    """
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "admin")
        invite = await access.create_invite(created_by=1)
        code = invite.code

    update_a = make_update_message(f"/start {code}", user_id=7, update_id=101)
    update_b = make_update_message(f"/start {code}", user_id=8, update_id=102)

    await asyncio.gather(
        harness.dp.feed_update(harness.bot, update_a),
        harness.dp.feed_update(harness.bot, update_b),
    )

    async with container() as rc:
        access = await rc.get(AccessService)
        allowed = [await access.is_allowed(7), await access.is_allowed(8)]
    assert sum(allowed) == 1


async def test_two_concurrent_first_contacts_from_the_same_new_user_both_succeed(
    harness, container
):
    """Reproduces the review's ensure_registered race: two updates from the
    same brand-new user_id (here, the default admin id=1), fed concurrently.
    Before the fix (non-atomic check-then-insert), the second's flush raised
    IntegrityError (UNIQUE constraint on users.id) and that whole update was
    swallowed by the generic error handler instead of getting its real reply.
    """
    update_a = make_update_message("/menu", user_id=1, update_id=201)
    update_b = make_update_message("/status", user_id=1, update_id=202)

    await asyncio.gather(
        harness.dp.feed_update(harness.bot, update_a),
        harness.dp.feed_update(harness.bot, update_b),
    )

    sent_texts = harness.session.sent_texts()
    assert bot_texts.ERR_UNEXPECTED not in sent_texts
    assert bot_texts.MENU_ROOT in sent_texts
    assert any("Добавь группу" in t for t in sent_texts)
