"""Shared shapes of the "ask for a value" dialogs.

Handler-flow helpers, not transport: their callers are the handler modules
(groups, chains, rides), and they drive FSM state. They take the Responder
rather than living on it — recording an intent and running a dialog step are
different jobs.
"""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message

from chain_health.bot import texts
from chain_health.bot.parsing import parse_positive_float
from chain_health.bot.ui import Responder


async def start_edit_dialog(
    callback: CallbackQuery,
    state: FSMContext,
    next_state: State,
    prompt: str,
    data: dict[str, int],
    responder: Responder,
) -> None:
    """Shared shape of every "edit this value" entry point: stash the
    authorized entity's id in FSM state, prompt for the new value, and answer
    the callback so its spinner stops.
    """
    await state.update_data(data)
    await state.set_state(next_state)
    responder.edit(callback.message, prompt)
    responder.answer_callback(callback)


def ask_number_or_retry(
    message: Message, responder: Responder, *, max_value: float
) -> float | None:
    """Parses the numeric FSM reply, or asks again and returns None.

    max_value has no default and is required, on purpose: a bare
    parse_positive_float(message.text) call once let a ride-edit/limit-edit
    dialog accept an unbounded number, permanently corrupting cycle_km/total_km
    or silencing over-limit warnings forever. Every caller must pick a sane
    bound rather than being able to forget one.
    """
    value = parse_positive_float(message.text, max_value=max_value)
    if value is None:
        responder.reply_raw(message.chat.id, texts.ASK_NUMBER_RETRY)
    return value
