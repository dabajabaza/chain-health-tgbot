from chain_health.bot.callbacks import GroupAction, GroupCB, MenuAction, MenuCB
from chain_health.services.garage import GarageService
from tests.helpers.factories import fsm_state, onboard


async def test_navigating_to_menu_root_clears_a_started_dialog(harness, container):
    """Reproduces the review scenario: start "add group" (state=AddGroup.name),
    then navigate away via an inline button (MenuCB ROOT) without answering
    the prompt. Sending a plain number afterward must be recorded as a ride,
    not swallowed as the abandoned dialog's answer.
    """
    await onboard(harness)
    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    assert await fsm_state(harness) is not None

    await harness.click(MenuCB(action=MenuAction.ROOT).pack(), user_id=1)
    assert await fsm_state(harness) is None

    harness.session.clear()
    await harness.send("42", user_id=1)

    async with container() as rc:
        garage = await rc.get(GarageService)
        groups = await garage.list_groups(1)
        assert [g.name for g in groups] == ["Шоссе"]  # no "42" group created
    sent_texts = harness.session.sent_texts()
    assert any("42" in t for t in sent_texts)


async def test_status_button_escapes_a_started_dialog(harness, container):
    """The reply-keyboard's own buttons must always work, even mid-dialog —
    otherwise pressing 📊 Статус while a text prompt is open just becomes the
    dialog's next answer.
    """
    await onboard(harness)
    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    harness.session.clear()

    await harness.send("📊 Статус", user_id=1)

    assert await fsm_state(harness) is None
    async with container() as rc:
        garage = await rc.get(GarageService)
        groups = await garage.list_groups(1)
        assert all(g.name != "📊 Статус" for g in groups)
    sent = harness.session.calls_of("SendMessage")
    assert any("Шоссе" in (m.text or "") for m in sent)


async def test_menu_button_escapes_a_started_dialog(harness, container):
    await onboard(harness)
    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    harness.session.clear()

    await harness.send("☰ Меню", user_id=1)

    assert await fsm_state(harness) is None
    async with container() as rc:
        garage = await rc.get(GarageService)
        groups = await garage.list_groups(1)
        assert all(g.name != "☰ Меню" for g in groups)


async def test_restarting_start_as_a_returning_user_clears_a_stuck_dialog(harness, container):
    await onboard(harness)
    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    assert await fsm_state(harness) is not None

    await harness.send("/start", user_id=1)

    assert await fsm_state(harness) is None


async def test_an_admin_command_interrupts_an_open_dialog(harness, session_factory):
    """The admin router is included first and takes the update whole, so it has
    to clear the state itself — otherwise the user's next message is swallowed
    as the answer to a dialog they abandoned."""
    await onboard(harness)
    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)
    assert await fsm_state(harness) is not None, "the dialog must be open"

    await harness.send("/invite", user_id=1)

    assert await fsm_state(harness) is None, "an admin command must close the dialog"
