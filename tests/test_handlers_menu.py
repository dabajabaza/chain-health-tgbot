from chain_health.bot import texts as bot_texts
from chain_health.bot.callbacks import (
    ChainAction,
    ChainCB,
    GroupAction,
    GroupCB,
    RideAction,
    RideCB,
    RotateAction,
    RotateCB,
)
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from chain_health.services.rides import RideService
from tests.factories import onboard


async def _first_group_and_chain(container, user_id: int = 1):
    async with container() as rc:
        garage = await rc.get(GarageService)
        group = (await garage.list_groups(user_id))[0]
        chain = (await garage.list_chains(group))[0]
        return group, chain


async def test_status_button_and_status_command_produce_the_same_reply(harness):
    await onboard(harness)

    await harness.send("📊 Статус", user_id=1)
    via_button = harness.session.sent_texts()[-1]
    harness.session.clear()

    await harness.send("/status", user_id=1)
    via_command = harness.session.sent_texts()[-1]

    assert via_button == via_command


async def test_menu_button_and_menu_command_open_the_root_menu(harness):
    await harness.send("☰ Меню", user_id=1)
    via_button = harness.session.sent_texts()[-1]
    harness.session.clear()

    await harness.send("/menu", user_id=1)
    via_command = harness.session.sent_texts()[-1]

    assert via_button == via_command


async def test_add_group_then_chain_flow_across_three_updates(harness, container):
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.ensure_registered(1, "admin")

    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    await harness.send("Вторая группа", user_id=1)

    async with container() as rc:
        garage = await rc.get(GarageService)
        groups = await garage.list_groups(1)
        assert any(g.name == "Вторая группа" for g in groups)


async def test_rotation_menu_marks_the_recommended_chain(harness, container):
    await onboard(harness)
    group, _chain_a = await _first_group_and_chain(container)
    async with container() as rc:
        garage = await rc.get(GarageService)
        group = await garage.require_group(1, group.id)
        await garage.add_chain(group, "Б")

    await harness.click(RotateCB(action=RotateAction.MENU, group_id=group.id).pack(), user_id=1)

    edits = harness.session.calls_of("EditMessageText")
    assert edits
    kb = edits[-1].reply_markup
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("⭐" in label for label in labels)


async def test_rotation_confirm_resets_the_cycle(harness, container):
    await onboard(harness)
    await harness.send("100", user_id=1)
    harness.session.clear()

    group, _chain_a = await _first_group_and_chain(container)
    async with container() as rc:
        garage = await rc.get(GarageService)
        group = await garage.require_group(1, group.id)
        chain_b = await garage.add_chain(group, "Б")
        chain_b_id = chain_b.id

    await harness.click(
        RotateCB(action=RotateAction.CONFIRM, group_id=group.id, chain_id=chain_b_id).pack(),
        user_id=1,
    )

    async with container() as rc:
        garage = await rc.get(GarageService)
        group = await garage.require_group(1, group.id)
        active = await garage.active_chain(group)
        assert active.id == chain_b_id


async def test_stale_rotate_confirm_button_for_the_now_active_chain_is_denied(harness, container):
    """The rotation-candidates keyboard stays live in chat history forever —
    pressing an old confirm button for a chain that has meanwhile become
    active must not zero its accumulated cycle_km (see docs findings on
    GarageService.rotate).
    """
    await onboard(harness)
    await harness.send("100", user_id=1)
    harness.session.clear()

    group, chain_a = await _first_group_and_chain(container)
    payload = RotateCB(action=RotateAction.CONFIRM, group_id=group.id, chain_id=chain_a.id).pack()

    await harness.click(payload, user_id=1)

    async with container() as rc:
        rides = await rc.get(RideService)
        garage = await rc.get(GarageService)
        chain_a = await garage.require_chain(1, chain_a.id)
        assert await rides.cycle_km(chain_a) == 100
    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1


async def test_rotate_confirm_with_mismatched_group_id_is_denied(harness, container):
    """RotateCB's group_id is only load-bearing for the MENU action — CONFIRM
    resolves the chain (and its rotation) from chain_id alone. A payload
    claiming a group_id that doesn't match the chain's actual group must
    still be rejected rather than silently ignored (see D15), not just
    happen to rotate the right chain anyway.
    """
    await onboard(harness)
    group, chain_a = await _first_group_and_chain(container)
    async with container() as rc:
        garage = await rc.get(GarageService)
        other_group = await garage.create_group(1, "Другая группа")
        await garage.add_chain(other_group, "П")  # becomes active automatically
        chain_b = await garage.add_chain(other_group, "Б")  # not active
        chain_b_id = chain_b.id

    payload = RotateCB(action=RotateAction.CONFIRM, group_id=group.id, chain_id=chain_b_id).pack()
    await harness.click(payload, user_id=1)

    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1
    async with container() as rc:
        garage = await rc.get(GarageService)
        chain_b = await garage.require_chain(1, chain_b_id)
        assert not chain_b.is_active
        chain_a = await garage.require_chain(1, chain_a.id)
        assert chain_a.is_active


async def test_a_very_long_group_name_is_rejected_with_a_retry_message(harness, container):
    """No DB-level length limit exists (SQLite doesn't enforce VARCHAR(N)) —
    without a handler-level check, a long enough name pushes group_detail_text
    past Telegram's message-length limit and the whole creation rolls back
    with no clear feedback about why.
    """
    long_name = "A" * 200

    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    await harness.send(long_name, user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_NAME_TOO_LONG_RETRY
    async with container() as rc:
        garage = await rc.get(GarageService)
        assert await garage.list_groups(1) == []


async def test_editing_the_chain_limit_rejects_an_absurd_value(harness, container):
    await onboard(harness)
    _group, chain = await _first_group_and_chain(container)

    await harness.click(ChainCB(action=ChainAction.SET_LIMIT, chain_id=chain.id).pack(), user_id=1)
    await harness.send("9999999", user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_NUMBER_RETRY
    async with container() as rc:
        garage = await rc.get(GarageService)
        unchanged = await garage.require_chain(1, chain.id)
        assert unchanged.cycle_limit_km == 300.0


async def test_callback_for_another_users_group_is_denied_without_leaking_data(harness, container):
    await onboard(harness)
    group, _chain = await _first_group_and_chain(container)
    async with container() as rc:
        access = await rc.get(AccessService)
        await access.allow_user(2, "friend")

    await harness.click(GroupCB(action=GroupAction.VIEW, group_id=group.id).pack(), user_id=2)

    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1
    edits = harness.session.calls_of("EditMessageText")
    assert not any(group.name in (m.text or "") for m in edits)


async def test_delete_callback_for_another_users_ride_is_denied(harness, container):
    await onboard(harness)
    await harness.send("42", user_id=1)
    harness.session.clear()

    async with container() as rc:
        access = await rc.get(AccessService)
        await access.allow_user(2, "friend")
        rides = await rc.get(RideService)
        ride = (await rides.recent_rides(1))[0]
        ride_id = ride.id

    await harness.click(RideCB(action=RideAction.DELETE, ride_id=ride_id).pack(), user_id=2)

    async with container() as rc:
        rides = await rc.get(RideService)
        still_there = await rides.require_ride(1, ride_id)
        assert still_there.id == ride_id
