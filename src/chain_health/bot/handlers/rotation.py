from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from dishka import FromDishka

from chain_health.bot import keyboards, texts
from chain_health.bot.callbacks import RotateAction, RotateCB
from chain_health.bot.ui import Responder
from chain_health.domain.errors import NotFoundError
from chain_health.services.garage import GarageService

router = Router(name="rotation")


@router.callback_query(RotateCB.filter(F.action == RotateAction.MENU))
async def cb_rotate_menu(
    callback: CallbackQuery,
    callback_data: RotateCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    group = await garage.require_group(callback.from_user.id, callback_data.group_id)
    options = await garage.rotation_options(group)
    if not options.candidates:
        responder.edit(callback.message, texts.NO_ROTATION_CANDIDATES)
        responder.answer_callback(callback)
        return

    keyboard = keyboards.rotation_candidates_keyboard(
        group.id, options.candidates, options.recommended, options.totals
    )
    responder.edit(callback.message, texts.ROTATION_PROMPT, keyboard)
    responder.answer_callback(callback)


@router.callback_query(RotateCB.filter(F.action == RotateAction.CONFIRM))
async def cb_rotate_confirm(
    callback: CallbackQuery,
    callback_data: RotateCB,
    state: FSMContext,
    garage: FromDishka[GarageService],
    responder: FromDishka[Responder],
) -> None:
    await state.clear()
    user_id = callback.from_user.id
    chain = await garage.require_chain(user_id, callback_data.chain_id)
    if chain.group_id != callback_data.group_id:
        # group_id is only load-bearing for cb_rotate_menu; here it's a second,
        # independent claim about the same chain riding along in the payload.
        # require_chain already proves ownership, but a mismatch still means
        # the button is stale (or the payload was tampered with) — treat it
        # the same as a not-found chain rather than silently ignoring the
        # field (see D15 on not distinguishing "missing" from "not yours").
        raise NotFoundError("chain", callback_data.chain_id)
    await garage.rotate(chain)

    message = callback.message
    responder.clear_keyboard(message)
    chat_id = message.chat.id if message is not None else user_id

    await responder.reply(
        chat_id, user_id, texts.rotation_confirmed_text(chain.name), data_changed=True
    )
    responder.answer_callback(callback)
