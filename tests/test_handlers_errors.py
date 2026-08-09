from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import GetMe

from chain_health.bot import texts as bot_texts
from chain_health.bot.callbacks import ChainAction, ChainCB, GroupAction, GroupCB, RideAction
from chain_health.bot.states import EditRideAmount
from chain_health.services.access import AccessService
from chain_health.services.garage import GarageService
from chain_health.services.rides import RideService
from tests.helpers.factories import fsm_state, onboard

_FAKE_METHOD = GetMe()


async def test_stale_button_on_a_retired_chain_renders_instead_of_crashing(harness, container):
    """The pre-fix code did `next(s for s in statuses if s.chain.id == chain_id)`
    over a retired-excluding list — pressing a "view chain" button for a
    chain retired in the meantime raised StopIteration. require_chain finds
    retired chains too, so this must render cleanly.
    """
    await onboard(harness)
    async with container() as rc:
        garage = await rc.get(GarageService)
        group = (await garage.list_groups(1))[0]
        chain = (await garage.list_chains(group))[0]
        await garage.retire_chain(chain)
        chain_id, group_id = chain.id, group.id

    payload = ChainCB(action=ChainAction.VIEW, chain_id=chain_id, group_id=group_id).pack()
    await harness.click(payload, user_id=1)

    edits = harness.session.calls_of("EditMessageText")
    assert edits
    assert "списана" in edits[-1].text
    assert len(harness.session.calls_of("AnswerCallbackQuery")) == 1


async def test_unknown_chain_id_is_denied_with_exactly_one_answered_callback(harness, container):
    await onboard(harness)

    await harness.click(
        ChainCB(action=ChainAction.VIEW, chain_id=999999, group_id=1).pack(), user_id=1
    )

    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1
    assert harness.session.calls_of("EditMessageText") == []


async def test_malformed_callback_payload_falls_back_gracefully(harness):
    """A payload that looks like a real prefix but fails CallbackData's own
    field parsing (non-integer id) doesn't match that filter at all, so it
    must reach the catch-all fallback router rather than raising anywhere.
    """
    await harness.click("chain:view:not-a-number:1", user_id=1)

    answers = harness.session.calls_of("AnswerCallbackQuery")
    assert len(answers) == 1
    assert harness.session.calls_of("EditMessageText") == []


async def test_a_dialog_started_by_the_failing_handler_itself_still_gets_cleared(harness):
    """cb_group_add sets state=AddGroup.name *before* edit_text_or_ignore can
    fail. The FSM middleware snapshots the state as it was *before* the
    handler ran (None here, no dialog yet) — on_error must read the live
    state instead of trusting that snapshot, or the just-set AddGroup.name
    dangles and swallows the user's next message as a group name.
    """
    await onboard(harness)
    assert await fsm_state(harness) is None
    harness.session.fail_on["EditMessageText"] = TelegramBadRequest(
        method=_FAKE_METHOD, message="Bad Request: message to edit not found"
    )

    await harness.click(GroupCB(action=GroupAction.ADD).pack(), user_id=1)

    assert await fsm_state(harness) is None
    assert harness.session.calls_of("AnswerCallbackQuery")


async def test_editing_a_ride_amount_with_a_foreign_ride_id_in_state_is_denied(harness, container):
    """FSM state is user-scoped storage but is written from callback-supplied
    ids — handlers must re-authorize at use, not trust the stored value.
    Exercised here by editing another user's ride id directly into state via
    the same mechanism the real handler uses, then completing the dialog.
    """
    await onboard(harness)
    await harness.send("42", user_id=1)
    harness.session.clear()

    async with container() as rc:
        access = await rc.get(AccessService)
        await access.allow_user(2, "friend")
        rides = await rc.get(RideService)
        victim_ride = (await rides.recent_rides(1))[0]
        ride_id = victim_ride.id

    storage = harness.dp.fsm.storage
    key = StorageKey(bot_id=harness.bot.id, chat_id=2, user_id=2)
    await storage.set_data(key, {"ride_id": ride_id})
    await storage.set_state(key, EditRideAmount.value)

    await harness.send("999", user_id=2)

    assert harness.session.sent_texts() != []  # denied with a reply, not silence
    async with container() as rc:
        rides = await rc.get(RideService)
        untouched = await rides.require_ride(1, ride_id)
        assert untouched.distance_km == 42


async def test_a_forged_out_of_range_id_gets_the_stale_button_answer(harness):
    """Ids in callback_data are client-forgeable and land in a SQLite bind
    parameter. Python ints are unbounded, so a 23-digit value — comfortably
    inside Telegram's 64-byte limit — used to pass validation and raise
    OverflowError deep inside the query: the user saw the generic failure toast
    and the journal got a traceback, instead of the stale-button answer.
    """
    await onboard(harness)
    harness.session.clear()

    await harness.click(f"ride:{RideAction.DELETE.value}:{'9' * 23}", user_id=1)

    answers = [m.text or "" for m in harness.session.calls_of("AnswerCallbackQuery")]
    assert answers, "the spinner must stop"
    assert bot_texts.ERR_UNEXPECTED not in answers, f"expected a graceful answer, got {answers}"
